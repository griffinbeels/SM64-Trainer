# Private ghost beta for testers

Send the same installer from `dist/private-beta/` to as many testers as you want.
Each person opens that one EXE and uses their own account.
It installs the application locally, creates **SM64 Trainer Beta**, and opens setup.
Python, a source checkout and separate FFmpeg/plugin downloads are unnecessary.
If Windows lacks the embedded-browser runtime, the same setup opens in their browser.

The friend supplies their lawful Project64/Usamune installation through the setup
screen. The build includes the trainer's graphics wrapper and renderer, not a ROM.
Setup checks the actual running ROM, capture and movement before completing.
Close another running trainer before recording: both channels share one recorder
and the selected emulator installation. Their beta practice history stays separate.

Sign in with Discord, Google or Twitch, choose a player name, and accept the
displayed policies. This build's reusable invitation admits independent accounts
without a tester-count limit or calendar deadline, until the operator revokes it.
Keep the installer private; no separate invitation or build is needed per person.
Request and storage limits still apply to every account and the service.
Google testing mode can require its test-user
allowlist; Discord or Twitch avoids that Google-specific setup.

The included library contains legitimate recordings credited to
**griffman1212**, only those whose controller input is known on every frame
(the installer's README names the count). They become local rivals automatically and work without cloud
downloads. They do not fill the tester's practice history or PBs. Historical
unknown ROMs and controller inputs stay unknown; those clips are never submitted
as certified new runs. New eligible tester runs upload under the tester's account.
In this beta, other testers' contributions are collected without becoming rivals
for everyone. Own PB playback stays independent from automatic rival selection.

Try these flows:

1. Complete setup, select a practiced star and strategy, and begin an attempt.
2. Save a PB. Confirm your PB appears beside the credited automatic rivals.
3. Improve the PB. Confirm the roster changes when useful new rivals exist.
4. Beat the available ghosts. The fastest compatible available rivals remain,
   together with your own PB, even outside the preferred rank range.
5. Switch Strategy/Overall, choose a rival, and reset that choice with its icon.
6. Disconnect temporarily. Practice and included ghosts continue; eligible new
   uploads retry when the connection returns, including after restarting and
   signing into the same account. Keep the original recording files available.
   Signing out or disabling sharing cancels queued submissions.
7. Close the beta and reopen its shortcut. Confirm your account and beta history
   return, without changing the public trainer's history.

For a report, include the build ID, star, strategy, expected versus observed
behavior, and whether PJ64 and the internet were running. Screenshots and the
beta log help. Never send passwords, OAuth URLs/codes, tokens or operator keys.

Maintainer security, quotas and setup details live in
[the community service](../cloud/community/README.md),
[community protocol](community-protocol.md), and [private builds](private-beta.md).
Automated fixtures and managed OAuth probes do not certify native gameplay,
human social-login completion or every machine configuration; those are the
purpose of the testers' beta sessions.
