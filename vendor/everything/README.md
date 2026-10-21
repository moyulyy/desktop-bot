# Everything 1.5 (vendor)

This directory contains the portable Everything 1.5 runtime used by the `finder` subagent.

- `Everything.exe`, `Everything.lng`, `*.ini`, `Session-*.json` — Everything 1.5.0.1409a (x86)
- `Everything3_x64.dll`, `Everything3_x86.dll` — official Everything SDK3 (named-pipe API)

The application starts `Everything.exe` on demand and hides its main window.
Everything is third-party software by voidtools; see `SDK3-LICENSE.txt` for the SDK license.
