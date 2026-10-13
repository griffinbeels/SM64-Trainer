// Account limits are checked again by the local API and community service
// (community/names.py is the Python twin; tests/test_community_names.py pins
// both). The minimum comes from Speedrun.com's own sign-up form, which
// refuses to submit a name shorter than 4 characters (its signup page code,
// read 2026-10-12); his rule: "we should use the same username rules as
// Speedrun.com".
export const NAME_RULES = Object.freeze({ minCharacters: 4, maxCharacters: 20, maxBytes: 63 });
export const NAME_HINT = `${NAME_RULES.minCharacters} to ${NAME_RULES.maxCharacters} characters.`;

export function normalizedPlayerName(value) {
  return value.normalize("NFKC").trim();
}

export function playerNameError(value) {
  if (/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value)) return "Use a name without control characters.";
  if (new TextEncoder().encode(value).length > 128) return "Player name input is too long.";
  const name = normalizedPlayerName(value);
  if (!name) return "Enter your player name.";
  const characters = Array.from(name).length;
  if (characters < NAME_RULES.minCharacters) return `Use at least ${NAME_RULES.minCharacters} characters.`;
  if (characters > NAME_RULES.maxCharacters) return `Use ${NAME_RULES.maxCharacters} characters or fewer.`;
  if (new TextEncoder().encode(name).length > NAME_RULES.maxBytes) return "This name is too long for the in-game display.";
  return "";
}
