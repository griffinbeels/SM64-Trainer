export class ServiceError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
export function fail(status, code, message) { throw new ServiceError(status, code, message); }
export function requireThat(ok, code = "invalid_request", message = "Invalid request.", status = 400) {
  if (!ok) fail(status, code, message);
}
export function json(value, status = 200, headers = {}) {
  return Response.json({v: 1, ...value}, {status, headers: {
    "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", ...headers
  }});
}
export async function boundedBody(request, limit, expected) {
  const declared = request.headers.get("content-length");
  if (declared !== null) requireThat(/^\d+$/.test(declared) && Number(declared) <= limit &&
    (expected === undefined || Number(declared) === expected), "body_limit", "Body length exceeds its grant.", 413);
  requireThat(!request.headers.get("content-encoding"), "unsupported_encoding", "HTTP content encoding is unsupported.");
  const chunks = []; let size = 0;
  const reader = request.body?.getReader();
  if (reader) try {
    for (;;) { const {done, value} = await reader.read(); if (done) break;
      size += value.byteLength;
      if (size > limit || (expected !== undefined && size > expected)) {
        await reader.cancel(); fail(413, "body_limit", "Body exceeds its bound.");
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  requireThat(expected === undefined || size === expected, "body_length", "Body length disagrees with its grant.");
  const bytes = new Uint8Array(size); let at = 0;
  for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.length; }
  return bytes;
}
export async function readJSON(request, fields, required = fields) {
  requireThat(request.headers.get("content-type")?.split(";")[0] === "application/json", "content_type", "Expected application/json.");
  let value; try { value = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(await boundedBody(request, 16384))); }
  catch (error) { if (error instanceof ServiceError) throw error; fail(400, "invalid_json", "Invalid JSON."); }
  requireThat(value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).every(k => fields.includes(k)) && required.every(k => Object.hasOwn(value,k)));
  return value;
}
export function exactQuery(url, fields, required = []) {
  for (const [key] of url.searchParams) requireThat(fields.includes(key) && url.searchParams.getAll(key).length === 1);
  for (const key of required) requireThat(url.searchParams.has(key));
}
export const hex64 = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
export const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max;
export function canonical(value) {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object") return "{" + Object.keys(value).sort().map(k => JSON.stringify(k)+":"+canonical(value[k])).join(",") + "}";
  return JSON.stringify(value);
}
export async function sha256(value) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map(x=>x.toString(16).padStart(2,"0")).join("");
}
