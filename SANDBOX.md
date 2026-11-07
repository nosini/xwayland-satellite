# Popup lifetime fixes

This fork fixes parent-first popup teardown for Wine launchers such as NIKKE.
Popup descendants are destroyed before their parent, their X11 windows are
dismissed, and queued events for destroyed roles are ignored.

Build from this checkout with `cargo build --locked --release`.
The library regression tests run with `cargo test --locked --lib`.
A live Mutter regression check is available on the test VM:

```sh
dbus-run-session -- python3 tests/popup-lifetime-check.py
```

To reproduce the stock 0.8.3 failure instead:

```sh
dbus-run-session -- python3 tests/popup-lifetime-check.py \
  --satellite /usr/bin/xwayland-satellite --expect-crash
```

The run-in-sandbox build helper fetches this fork from
https://github.com/nosini/xwayland-satellite.
