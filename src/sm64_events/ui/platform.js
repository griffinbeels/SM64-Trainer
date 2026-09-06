// The platform stamp -- WHICH MACHINE set a time -- the browser's copy of
// core/modes.py's PLATFORMS / PLATFORM_LABELS / DEFAULT_PLATFORM /
// platform_of. The two copies are compared by
// tests/test_cross_language_parity.py, and the two literals may appear in no
// other file (tests/test_single_source.py). Import-free on purpose so node
// can import this module directly.
export const PLATFORMS = ["emu", "n64"];
export const PLATFORM_LABELS = { emu: "Emulator", n64: "N64" };
export const DEFAULT_PLATFORM = "emu";
// The two values by name, for a module that compares a stamp (round 29's
// sheet colours) without spelling the literal a second time.
export const [EMU, N64] = PLATFORMS;

// An absent stamp is the emulator: until the console front-end existed,
// nothing but Project64's memory could close an attempt. ONE rule, mirrored
// from platform_of, so no surface grows its own `|| "emu"`.
export function platformOf(stored) {
  return PLATFORMS.includes(stored) ? stored : DEFAULT_PLATFORM;
}
