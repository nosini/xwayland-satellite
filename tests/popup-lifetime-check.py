#!/usr/bin/env python3
"""Reproduce parent-first popup closure on a private headless Mutter.

Run under dbus-run-session on the build/test VM. --expect-crash checks the
unpatched system satellite, and the default checks our private build.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def client():
    import ctypes as c
    import os
    import time

    x = c.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes = [c.c_char_p]
    x.XOpenDisplay.restype = c.c_void_p
    d = x.XOpenDisplay(None)
    assert d
    x.XDefaultRootWindow.argtypes = [c.c_void_p]
    x.XDefaultRootWindow.restype = c.c_ulong
    root = x.XDefaultRootWindow(d)
    x.XCreateSimpleWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint,
                                     c.c_uint, c.c_ulong, c.c_ulong]
    x.XCreateSimpleWindow.restype = c.c_ulong
    for name in ('XMapWindow', 'XDestroyWindow', 'XUnmapWindow'):
        getattr(x, name).argtypes = [c.c_void_p, c.c_ulong]
    x.XSync.argtypes = [c.c_void_p, c.c_int]
    x.XChangeWindowAttributes.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_void_p]
    x.XStoreName.argtypes = [c.c_void_p, c.c_ulong, c.c_char_p]

    class Attr(c.Structure):
        _fields_ = [('background_pixmap',c.c_ulong),('background_pixel',c.c_ulong),
                    ('border_pixmap',c.c_ulong),('border_pixel',c.c_ulong),
                    ('bit_gravity',c.c_int),('win_gravity',c.c_int),('backing_store',c.c_int),
                    ('backing_planes',c.c_ulong),('backing_pixel',c.c_ulong),('save_under',c.c_int),
                    ('event_mask',c.c_long),('do_not_propagate_mask',c.c_long),
                    ('override_redirect',c.c_int),('colormap',c.c_ulong),('cursor',c.c_ulong)]

    top = x.XCreateSimpleWindow(d, root, 0, 0, 500, 400, 0, 0, 0xffffff)
    x.XStoreName(d, top, b'Popup lifetime reproduction')
    class Hints(c.Structure):
        _fields_=[('flags',c.c_long),('input',c.c_int),('initial_state',c.c_int),
                  ('icon_pixmap',c.c_ulong),('icon_window',c.c_ulong),('icon_x',c.c_int),
                  ('icon_y',c.c_int),('icon_mask',c.c_ulong),('window_group',c.c_ulong)]
    x.XSetWMHints.argtypes=[c.c_void_p,c.c_ulong,c.POINTER(Hints)]
    x.XSetWMHints(d,top,c.byref(Hints(flags=1,input=1)))
    x.XMapWindow(d, top)
    x.XSync(d, 0)
    time.sleep(0.6)
    popup = x.XCreateSimpleWindow(d, root, 10, 10, 100, 80, 0, 0, 0xff0000)
    attrs = Attr(override_redirect=1)
    x.XChangeWindowAttributes(d, popup, 1 << 9, c.byref(attrs))
    x.XMapWindow(d, popup)
    x.XSync(d, 0)
    time.sleep(0.6)
    x.XUnmapWindow(d, top)
    x.XSync(d, 0)
    time.sleep(0.6)
    # A server that survives must still accept requests for an unrelated window.
    other = x.XCreateSimpleWindow(d, root, 0, 0, 200, 100, 0, 0, 0)
    x.XMapWindow(d, other)
    x.XSync(d, 0)
    time.sleep(0.1)
    print('parent-first closure survived', flush=True)


def run(args, base):
    from gi.repository import Gio, GLib
    runtime = base / 'runtime'
    runtime.mkdir(mode=0o700)
    env = {**os.environ, 'XDG_RUNTIME_DIR': str(runtime), 'WAYLAND_DISPLAY': 'popup-check',
           'LIBGL_ALWAYS_SOFTWARE': '1', 'RUST_LOG': 'debug'}
    display = ':' + str(100 + os.getpid() % 10000)
    while Path('/tmp/.X' + display[1:] + '-lock').exists():
        display = ':' + str(int(display[1:]) + 1)
    satellite = None
    with (base / 'mutter.log').open('w') as mlog, (base / 'satellite.log').open('w') as slog:
        mutter = subprocess.Popen(
            ['mutter', '--headless', '--wayland', '--no-x11', '--virtual-monitor=1280x720',
             '--wayland-display=popup-check'], env=env, stdout=mlog, stderr=mlog, start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while not (runtime / 'popup-check').exists():
                if mutter.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('private Mutter failed to start')
                time.sleep(0.05)
            # A headless Mutter has no keyboard initially. Give this private
            # compositor a virtual keyboard so it can focus the X11 parent;
            # without focus satellite promotes the test popup to a toplevel.
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            dest = 'org.gnome.Mutter.RemoteDesktop'
            deadline = time.monotonic() + 10
            while not bus.call_sync(
                'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'NameHasOwner', GLib.Variant('(s)', (dest,)), GLib.VariantType.new('(b)'),
                Gio.DBusCallFlags.NONE, 5000, None
            ).unpack()[0]:
                if mutter.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('private Mutter remote input service did not start')
                time.sleep(0.05)
            def call(path, interface, method, params=None):
                return bus.call_sync(dest, path, interface, method, params, None,
                                     Gio.DBusCallFlags.NONE, 5000, None)
            session = call('/org/gnome/Mutter/RemoteDesktop', dest, 'CreateSession').unpack()[0]
            call(session, dest + '.Session', 'Start')
            for pressed in (True, False):
                call(session, dest + '.Session', 'NotifyKeyboardKeycode',
                     GLib.Variant('(ub)', (42, pressed)))
            satellite = subprocess.Popen([args.satellite, display, '-nolisten', 'tcp'],
                                          env=env, stdout=slog, stderr=slog, start_new_session=True)
            deadline = time.monotonic() + 10
            while 'Connected to Xwayland on' not in (base / 'satellite.log').read_text():
                if satellite.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('satellite failed to start')
                time.sleep(0.05)
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--client'],
                                    env={**env, 'DISPLAY': display}, text=True, capture_output=True, timeout=15)
            try:
                status = satellite.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                status = None
            log = (base / 'satellite.log').read_text()
            assert 'creating popup (' in log, 'the fixture did not create a real popup'
            if args.expect_crash:
                assert status == 101 and result.returncode != 0, (status, result)
                assert 'destroyed popup not top most popup' in log
                print('Reproduced the unpatched popup protocol error and exit status 101.')
            else:
                assert status is None and result.returncode == 0, (status, result)
                print('Popup parent closed first; satellite survived and opened another window.')
        except BaseException:
            print((base / 'mutter.log').read_text(), file=sys.stderr)
            print((base / 'satellite.log').read_text(), file=sys.stderr)
            raise
        finally:
            for process in (satellite, mutter):
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()


def main():
    if sys.argv[1:] == ['--client']:
        client()
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--satellite', default=str(Path(__file__).resolve().parents[1] / 'target/release/xwayland-satellite'))
    parser.add_argument('--expect-crash', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='satellite-popups-', dir=os.environ.get('SANDBOX_TEST_TMPDIR')) as tmp:
        run(args, Path(tmp))


if __name__ == '__main__':
    main()
