// src/sm64_events/ui/resources.js — what the Resources tab lists.
//
// Data only, import-free. Each entry of RESOURCE_SECTIONS is one card on the
// tab, drawn in this order: reorder the cards by moving rows, add a link by
// adding one. Every URL here was opened when its row was written; the rules
// summarize the linked official pages, which stay the authority.

const DISCORD = "/ui/assets/community/discord-clyde-white.svg";

export const RESOURCE_SECTIONS = [
  {
    id: "community",
    title: "Community",
    eyebrow: "Discord servers",
    kind: "links",
    items: [
      { name: "Super Mario 64 Speedruns", url: "https://discord.gg/rewdyqzbtT",
        note: "The main SM64 speedrunning community.", mark: DISCORD },
      { name: "SM64 League", url: "https://discord.gg/GAUwVCd7au",
        note: "The invite opens the SM64 Community Draft server.", mark: DISCORD },
      { name: "HackerN64", url: "https://discord.gg/CsGRCG53aE",
        note: "N64 romhacking and decompilation work.", mark: DISCORD },
      { name: "Usamune Projects", url: "https://discord.gg/tmxxT2X",
        note: "Home of the Usamune ROM and the Ultimate Star Spreadsheet.", mark: DISCORD },
      { name: "Griffman1212's Discord", url: "https://discord.gg/7KyWE4jWJs",
        note: "Support, ideas and feedback for SM64 Trainer.", mark: DISCORD },
    ],
  },
  {
    id: "learn",
    title: "Learn",
    eyebrow: "Videos",
    kind: "videos",
    items: [
      { name: "Dead Simple 16 Star Speedrun Tutorial", by: "Squeex",
        url: "https://www.youtube.com/watch?v=n_C0beWO9Mk" },
      { name: "How To Speedrun Super Mario 64 - 16 Star | Complete Guide", by: "FormFrame",
        url: "https://www.youtube.com/watch?v=_j_HQrHRmlY" },
      { name: "Super Mario 64 Speedrun Movement Guide", by: "SeanRaider",
        url: "https://www.youtube.com/watch?v=tyu1KO3x5Ps" },
    ],
  },
  {
    id: "reference",
    title: "Reference",
    eyebrow: "Sites and sheets",
    kind: "links",
    items: [
      { name: "Ultimate Star Spreadsheet",
        url: "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrkHXUzgcDmYa31gdtCI/edit?gid=1663140350#gid=1663140350",
        note: "Times for every star and strategy, from most top players." },
      { name: "Ukikipedia RTA Guide", url: "https://ukikipedia.net/wiki/RTA_Guide",
        note: "Routes and strategies for every star.", mark: "/ui/assets/ukikipedia.png" },
      { name: "The Daily Star", url: "https://sm64-xcams.netlify.app/about",
        note: "A casual event: a random star each day, 24 hours to post your fastest time." },
      { name: "Usamune ROM", url: "https://sites.google.com/view/supermario64/home",
        note: "Setup and settings for the practice ROM the trainer reads." },
      { name: "Project64 setup guide", url: "https://wermi.neocities.org/emuguide/",
        note: "Emulator, game and controller setup. The trainer's own setup links here." },
      { name: "Stage RTA leaderboards",
        url: "https://www.speedrun.com/sm64?h=Bob-omb_Battlefield-Stage_RTA-n64&x=l_r9gzzkjd-zdnq4oqd-e8m7em86.9qj7z0oq",
        note: "All seven stars of one course, on speedrun.com." },
    ],
  },
  {
    id: "rules",
    title: "Rules and settings",
    eyebrow: "Make your times count",
    kind: "rules",
    wide: true,
  },
  {
    id: "project",
    title: "SM64 Trainer",
    eyebrow: "This project",
    kind: "links",
    items: [
      { name: "Source code on GitHub", url: "https://github.com/griffinbeels/SM64-Trainer",
        note: "Open source. Contributions are welcome." },
      { name: "sm64trainer.app", url: "https://sm64trainer.app",
        note: "The public website and downloads." },
    ],
  },
];

// The Rules and settings card. One block per rule set; each block names the
// page it summarizes, so a reader can always check the current wording.
export const RULE_BLOCKS = [
  {
    title: "What the trainer records",
    points: [
      "Star times are x-cam times: the clock stops when Mario lands after the grab and the star dance starts.",
      "That is the timing the Ultimate Star Spreadsheet and the Daily Star use.",
    ],
  },
  {
    title: "Ultimate Star Spreadsheet",
    source: { label: "Info & Rules tab",
      url: "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrkHXUzgcDmYa31gdtCI/edit?gid=1571517726#gid=1571517726" },
    points: [
      "Enter x-cam times: 9.96, 59.96, 9:59.96. Star-grab times are not allowed.",
      "Castle times follow the latest Usamune ROM: the first movement frame is 0.00.",
      "Never add or delete rows or columns, and leave columns A to E to the mods.",
    ],
  },
  {
    title: "The Daily Star",
    source: { label: "About the Daily Star", url: "https://sm64-xcams.netlify.app/about" },
    points: [
      "Standard x-cam timing.",
      "Video is optional but recommended, especially for a top time.",
      "The Special Triple Jump is not allowed.",
    ],
  },
  {
    title: "Official single-star records",
    source: { label: "Singlestar rules",
      url: "https://sites.google.com/view/supermario64/singlestar/rules" },
    points: [
      "Nintendo 64 only: every form of emulation is banned, so Project64 times cannot go here.",
      "Timing runs from the second-to-last all-white frame to the first frame the star is collected.",
      "Real time, counted to two decimal places.",
    ],
  },
  {
    title: "Stage RTA",
    source: { label: "speedrun.com rules",
      url: "https://www.speedrun.com/sm64?h=Bob-omb_Battlefield-Stage_RTA-emu&rules=category&x=l_r9gzzkjd-zdnq4oqd-e8m7em86.5lmoxk01" },
    points: [
      "Start from the star select menu; timing ends on the first frame of stardust after the seventh star.",
      "Use Usamune's Stage RTA preset: MENU > MISC > PRESET > STGRTA.",
      "Emulator runs: Project64 1.6.x, Rosalie's Mupen GUI 0.5 or later, or OpenEmu 2 or later.",
    ],
  },
  {
    title: "Project64 settings for emulator runs",
    source: { label: "speedrun.com rules",
      url: "https://www.speedrun.com/sm64?h=Bob-omb_Battlefield-Stage_RTA-emu&rules=category&x=l_r9gzzkjd-zdnq4oqd-e8m7em86.5lmoxk01" },
    points: [
      "Counter Factor 1 (Edit Game Settings > ROM Settings). The default is 2.",
      "Limit FPS on (System > Limit FPS). F4 toggles it, right next to F5's load state.",
      "Default Save Type left at \"Use First Used Save Type\".",
    ],
  },
];

// Settings a submitted Stage RTA run must not use. Folded by default: it is
// the long tail, and the preset above already turns every one of them off.
export const STAGE_RTA_BANNED = [
  "Savestates, level reset and level entry",
  "Always-on, fade, section and misc timers",
  "Attempt counter, automatic menu, no music and nonstop",
  "Any HUD change except the lag counter, and the memory viewer",
  "Any STAGE setting other than NORMAL: CCMPENG, DDDSUB, DDDMANT, JRBMIST, STGTXT, STARTXT",
  "Anything that changes the game's look, such as Mario color swaps",
];
