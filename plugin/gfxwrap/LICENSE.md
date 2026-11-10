# License of this folder: GNU GPL v2 or later

SPDX-License-Identifier: GPL-2.0-or-later

Copyright (C) SM64 Trainer contributors.

The files in this folder are free software: you can redistribute them and/or
modify them under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

They are distributed in the hope that they will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU General Public License for more details:
version 2 is `licenses/renderer/GPL-2.0.txt`, version 3 is `LICENSE` at the
repository root.

Two things here are not ours and keep their own terms: `vendor/` (NVIDIA's
`nvEncodeAPI.h`, MIT, its notice in the file) and `zilmar.h` (it restates the
Zilmar graphics plugin interface from the spec header GLideN64 ships).

## Why this folder is not GPL v3 or later like the rest of the trainer

This code is linked into the graphics plugin, `sm64_trainer_gfx.dll`, with
LINK's GLideN64 v4.2, whose license is the GNU GPL version 2 only. Code under
GPL v3 cannot be combined with GPL v2-only code; code under "version 2 or
later" can be combined with both (the GNU GPL FAQ, "Is GPLv3 compatible with
GPLv2?"). The same code also builds the 64-bit encoder helper the trainer
loads, which ships under GPL v3 or later with the rest of the app; "v2 or
later" allows that too.

`licenses/README.md` names every other file of the trainer under this same
license (the pose model, the tools that build the plugin, and the scene-lab
hosts that load it).
