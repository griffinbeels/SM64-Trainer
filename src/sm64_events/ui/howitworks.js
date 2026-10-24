// src/sm64_events/ui/howitworks.js — the words and example data for
// About > How it works (drawn by components/howitworks.js).
//
// Data only, import-free. Every claim cites the code that makes it true, in
// the comment beside it; when that code changes, this is the file to re-read.
// Stay fundamental: describe what the trainer does, never a tuning number
// that could drift.

// Action codes are the game's own (src/sm64_events/memory/addresses.py).
export const ACTION_NAMES = {
  0x0C400201: "Standing still",
  0x03000880: "Jump",
  0x03000881: "Double jump",
  0x01000882: "Triple jump",
  0x0188088A: "Dive",
  0x04000470: "Landing from a jump",
  0x00001904: "Falling, holding a star",
  0x00001302: "Star dance",
  0x00001303: "Star dance (water)",
  0x00001307: "Star dance (stay in course)",
};

// US Usamune v1.93u addresses (memory/layout.py US, plus the MarioState
// offsets in memory/addresses.py). `slot` only places the cell in the
// picture; the grid is a drawing, not a memory map.
export const MEMORY_CELLS = [
  { label: "Mario's action", address: 0x8033B17C, slot: 21,
    meaning: "What Mario is doing this frame, as a number: jumping, diving, dancing with a star." },
  { label: "Star count", address: 0x8033B21A, slot: 47,
    meaning: "How many stars this save file has." },
  { label: "Current level", address: 0x8032DDF8, slot: 66,
    meaning: "Which level Mario is in." },
  { label: "Frame counter", address: 0x8032D5D4, slot: 83,
    meaning: "Counts up once per game frame, 30 times a second." },
];

// One midair grab, frame by frame (detectors/star_grab.py: the grab is an
// action edge; a midair grab passes through ACT_FALL_AFTER_STAR_GRAB and the
// x-cam is the star-dance entry). Frame numbers are an example.
export const GRAB_FRAMES = [
  { frame: 4410, action: 0x03000880, says: "Mario is in the air. Nothing to report." },
  { frame: 4411, action: 0x03000880, says: "Same action as last frame. Still nothing." },
  { frame: 4412, action: 0x00001904, mark: "grab",
    says: "The action changed to a star grab. The trainer notes which star it was and waits." },
  { frame: 4413, action: 0x00001904, says: "Still falling with the star." },
  { frame: 4414, action: 0x00001904, says: "Still falling with the star." },
  { frame: 4415, action: 0x00001302, mark: "xcam",
    says: "Mario landed and the star dance began: the x-cam moment, where the time counts." },
];

// The shape of what the page receives for a star (core/events.py to_wire,
// detectors/star_grab.py _emit), trimmed to the fields a reader recognizes.
export const MESSAGE_EXAMPLE = {
  type: "star_collected",
  frame: 4415,
  payload: {
    course_name: "Bob-omb Battlefield",
    star_name: "Shoot to the Island in the Sky",
    igt_frames: 343,
    igt: "0'11\"43",
    igt_source: "result",
  },
};

export const STOPS = [
  {
    short: "You play",
    title: "It starts with you playing",
    body: [
      "Super Mario 64 runs inside Project64 1.6, using Usamune, a practice version of the game.",
      "The trainer doesn't watch your screen to know what happened. Everything it knows, it reads out of the game's memory.",
    ],
    visual: "game",
    builtOn: ["Project64", "Usamune ROM"],
  },
  {
    short: "Memory",
    title: "The whole game is numbers in memory",
    body: [
      "The game keeps everything in the console's memory: what Mario is doing, where he is, which level he's in, how many stars you have.",
      "Each value lives at a fixed address. Fans rebuilt the game's source code from the cartridge, and that work tells us which address holds what.",
    ],
    term: ["Decompilation:", "turning a finished game back into source code people can read."],
    visual: "memory",
    builtOn: ["SM64 decompilation", "STROOP"],
  },
  {
    short: "Reading",
    title: "The trainer reads those numbers, every frame",
    body: [
      "First it finds the console's memory inside Project64 by the start-up values the console writes there when a game boots, and reads the cartridge's name to tell a US game from a JP one.",
      "Then, once per game frame, 30 times a second, it asks Windows for a copy of the values it needs. Nothing in the trainer writes to the game's memory, so it never changes your game.",
    ],
    visual: "live",
    builtOn: ["pymem"],
  },
  {
    short: "Noticing",
    title: "It notices when a number changes",
    body: [
      "Each copy is compared with the one before. Small watchers, called detectors, each look for one kind of change: star grabs, warps, deaths, resets, dustless jumps and more.",
      "A star grab is Mario's action switching to a star grab. The save file can't tell you that: it only changes the first time you collect a star, and practice is collecting the same star again and again. Step through the frames to see the detector work.",
    ],
    visual: "grab",
  },
  {
    short: "Timing",
    title: "The time comes from Usamune itself",
    body: [
      "Usamune keeps its own star timer in the extra memory of the Expansion Pak, which the original game never used. The trainer reads Usamune's number, so your time matches the one on screen.",
      "The clock is the game's frame: 30 per second, so one frame is about 0.033 seconds.",
    ],
    visual: "frames",
    builtOn: ["Usamune ROM"],
  },
  {
    short: "Sending",
    title: "The news goes straight to this page",
    body: [
      "The grab becomes an event: which star, what time, which frame. The trainer's server pushes it to this page the moment it's detected, before anything is saved.",
    ],
    term: ["WebSocket:", "a connection that stays open, so the server can push news without being asked."],
    visual: "message",
    builtOn: ["FastAPI"],
  },
  {
    short: "Saving",
    title: "Then it's written down, and you see it",
    body: [
      "The event goes into the journal, a database on your computer. Attempts, personal bests and ranks are all rebuilt from that journal.",
      "When this page hears about the grab, it asks the server for your updated attempts, best and rank, and redraws. Your time shows up.",
    ],
    visual: "journal",
    builtOn: ["Preact"],
  },
  {
    short: "Replays",
    title: "Replays take a separate road",
    body: [
      "Detection never needs the picture, but a replay does. Project64 draws the game through a graphics plugin, and the trainer installs its own version of one.",
      "That plugin stamps each finished picture with the game frame it shows, and your graphics card encodes it. The stamps become a frame map, so a replay lines up with your attempt exactly, with no guessing.",
    ],
    term: ["Graphics plugin:", "the part of Project64 that turns the game's drawing commands into the picture you see."],
    visual: "replay",
    builtOn: ["LINK's GLideN64 v4.2", "NVIDIA Video Codec SDK", "FFmpeg"],
  },
];
