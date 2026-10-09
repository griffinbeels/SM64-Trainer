// Account limits are checked again by the local API and community service.
export function normalizedPlayerName(value) {
  return value.normalize("NFKC").trim();
}

export function playerNameError(value) {
  if (/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value)) return "Use a name without control characters.";
  if (new TextEncoder().encode(value).length > 128) return "Player name input is too long.";
  const name = normalizedPlayerName(value);
  if (!name) return "Enter your player name.";
  if (Array.from(name).length > 20) return "Use 20 characters or fewer.";
  if (new TextEncoder().encode(name).length > 63) return "This name is too long for the in-game display.";
  return "";
}
