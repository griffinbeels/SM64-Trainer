"""First-time private-kit documents; no accounts, local paths or game assets."""

START_HERE = """PRIVATE M3 TEST -- START HERE

Use this ORIGINAL ZIP independently on both Windows 10/11 64-bit PCs.
Extract everything into a writable folder, such as your Desktop. Do not run
inside the ZIP. Keep app, live-transport, private and Run-M3-Test.bat together.
Send the original ZIP, not a copy re-zipped after you have used the app: the
used folder can contain your private live-session identity cache.

ALREADY SET UP ON THIS PC?
Close Trainer and Project64, run Run-M3-Test.bat, and update the Practice Replay
renderer through Trainer Setup while Project64 is closed. Your usual profile
stays on this PC. Then reopen Project64 and continue with JOIN A ROOM below.

FIRST TIME ON THIS PC
1. Get Project64 1.6 exactly. Open Get-Project64.url or use:
   https://wermi.neocities.org/emuguide/getting_emu/
   Put the emulator in a writable folder outside Program Files. The guide's
   preconfigured 1.6 package is the simplest starting point. Trainer does not
   bundle or download the emulator.

2. Supply your own Super Mario 64 ROM and apply the Usamune practice patch:
   https://wermi.neocities.org/emuguide/getting_game/
   https://sites.google.com/view/supermario64/usamunepj/usamunerom
   For this first test use standard Usamune v1.93u US on both PCs. The app also
   supports v1.93j JP. This ZIP contains no game ROM. VC/EU/Shindou builds from
   other guides are not substitutes for these supported practice-ROM builds.

3. Configure the emulator and your controller/keyboard using Emulator-Settings.url:
   https://wermi.neocities.org/emuguide/basic_config/
   Use 8 MB memory, Counter Factor 1 and the guide's standard US/JP CPU settings.
   Keep the game windowed. Uncheck Pause emulation when window is not active so
   switching to Trainer does not freeze your game. Controller help is at:
   https://wermi.neocities.org/emuguide/plugin_setup/

4. Close every Trainer and Project64 window. Double-click Run-M3-Test.bat.
   No Python, Epic Games Launcher, Epic account, developer SDK or pasted service
   credentials are needed. This account-enabled build starts with Discord or
   Twitch sign-in. Each tester should use their own account and claimed name;
   this ZIP does not contain anyone's sign-in or practice history.

5. Follow Trainer Setup -> Emulator. Open Project64 so Trainer can find it,
   then CLOSE Project64 for Install Practice Replay. Let Trainer select its
   graphics plugin; do not replace it manually with the generic guide's plugin.
   Reopen Project64, load your practice ROM, and move Mario while Setup checks
   game, inputs and pictures. Continue when Setup says Ready to practice.

JOIN A ROOM
On the first PC: Friends -> Start a room.
On the second PC: Friends -> Copy my player code.
Send that code to the first PC. Paste it into Friend's player code and choose
Invite friend. Accept the invitation on the second PC. Internet access is needed.
Enter the same course and area, turn Ghost Overlay on, and move Mario on both.
Each game runs independently; entering a room does not synchronize save states.
The host can remove players; current room members can invite other friends.

WHAT TO CHECK
- Both PCs see the other moving ghost in a matching course/area.
- Changing targets or closing the Friends panel keeps the room.
- Turning video recording off keeps saved-ghost poses/playback and live ghosts.
- Pause, resume, reset, and closing/reopening the ROM recover without stale poses.

ERROR REPORTS AND QUITTING
Error reports start enabled for a new profile. Settings -> Error reports lets
each tester opt out; a saved opt-out stays respected. Detectable failures send
bounded technical context automatically, without gameplay videos or raw logs.
No detector can prove every invisible rendering failure; describe anything that
still looks wrong during your test.
If a native WebView2/.NET window is unavailable, the same app opens in your
browser. Use Trainer's tray -> Quit to stop it; closing that tab is not Quit.

PRIVATE TEST ONLY
The dedicated untrusted player-client configuration is included so both PCs can
connect without developer setup. Share this ZIP privately, not on GitHub or a
public download site. No account tokens, saved profile or DeviceID cache are
included. Keep this candidate separate from other extracted Trainer builds.
"""


def portable_documents() -> dict[str, str]:
    """Deliberate user-opened shortcuts, written before the manifest and ZIP."""
    guides = {
        "Get-Project64.url": "https://wermi.neocities.org/emuguide/getting_emu/",
        "Create-Usamune-ROM.url": "https://sites.google.com/view/supermario64/usamunepj/usamunerom",
        "Emulator-Settings.url": "https://wermi.neocities.org/emuguide/basic_config/",
    }
    return {"START-HERE.txt": START_HERE,
            **{name: f"[InternetShortcut]\r\nURL={url}\r\n" for name, url in guides.items()}}
