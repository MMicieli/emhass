# CI experiment branch: Windows x64 / CPython 3.15.2 baseline

This branch contains only a GitHub Actions workflow and three small helper scripts. It holds no EMHASS source.
On a push to this branch the workflow checks out upstream `davidusb-geek/emhass` at a pinned commit (read-only),
builds the unmodified dependency set on CPython 3.15.2 with MSVC, executes the compiled stack and runs the
unmodified test suite. The token is read-only and no secrets are used. Related: EMHASS #1220.
