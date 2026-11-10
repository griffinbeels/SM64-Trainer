# Unicorn 2.1.4

Unicorn is a CPU emulator built on QEMU, by Nguyen Anh Quynh, Dang Hoang Vu
and its contributors (`AUTHORS.TXT`, `CREDITS.TXT`). The Ghost Lab runs the
game's own code in it: the free camera, rebuilt ghosts and save states.
The app carries its Python package, `unicorn`, with the engine as
`unicorn/lib/unicorn.dll` inside it. That package's wheel installs no
license file, so these texts stand in for it.

Version: 2.1.4

## Licenses

- **The engine (`unicorn.dll`) is GPL v2 only.** Its README says
  "Distributed under free software license GPLv2" and `COPYING` is the GPL
  v2. Some of the QEMU code it is built from is under "version 2" alone, for
  example `qemu/softmmu/memory.c` ("This work is licensed under the terms of
  the GNU GPL, version 2"), `qemu/include/exec/memory.h` and
  `qemu/include/qemu/processor.h`. QEMU's own note, `qemu-LICENSE`, says
  "The QEMU emulator as a whole is released under the GNU General Public
  License, version 2."
- Other parts of the engine are under the LGPL (`qemu-COPYING.LIB`, LGPL
  2.1; `COPYING.LGPL2` and `COPYING_GLIB`, the GNU Library GPL 2, for the
  GLib copy in `glib_compat/`) or under BSD-style, MIT-style and similar
  licenses. `THIRD-PARTY-NOTICES.txt` holds each permissive file's own
  notice.
- **The Python binding** (the package's Python code) is declared "BSD
  License" in its package metadata, which gives no further text.

A GPL v2-only program cannot be combined with GPL v3 or LGPL v3 code (the
GNU GPL FAQ, "Is GPLv3 compatible with GPLv2?" and its compatibility
matrix). So Unicorn must only ever run in a process that loads no GPL v3 or
LGPL v3 library, such as PyAV's FFmpeg or pystray, and the trainer's code
that runs with it there must be GPL v2 or later. `licenses/README.md` lists
that code.

## Source

The complete source is attached to each release as
`SM64Trainer-third-party-source-unicorn.tar`: Unicorn's own source release
on PyPI, `unicorn-2.1.4.tar.gz`, which the wheel is built from. Its engine
is the `src/` folder, the same files as the project's tag 2.1.4
(<https://github.com/unicorn-engine/unicorn/tree/2.1.4>).
`third-party-sources.json` pins the file by its SHA-256.

`THIRD-PARTY-NOTICES.txt` was collected from that source release. It holds
the leading license comment of every engine file (in `src/`, not its tests
or samples) under a permissive license, each comment listed once with the
files that carry it.
