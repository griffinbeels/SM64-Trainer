// A fake app server that keeps the real write door (server/local_access.py).
// Every POST, PUT or DELETE the app sends goes through ui/api.js::localFetch,
// which first fetches this server's request token from /api/local-access and
// carries it as X-SM64-Request-Token. This answers that bootstrap, so a test's
// own fake sees only the app's API calls, and refuses a write that lacks the
// token with the server's 403: a component that bypasses localFetch fails here
// as it would against the real server.
import { vi } from "vitest";

export const REQUEST_TOKEN = "fixture-request-token";
const READS = new Set(["GET", "HEAD", "OPTIONS"]);

const reply = (status, body) =>
  Promise.resolve({ ok: status < 400, status, json: async () => body });

export function stubLocalServer(fake) {
  // The node environment has no page address; localFetch compares origins.
  if (typeof globalThis.location === "undefined") {
    vi.stubGlobal("location", new URL("http://localhost/"));
  }
  // The fake receives the exact arguments the app passed: a bare GET keeps
  // its missing options.
  const server = vi.fn((...args) => {
    const [url, options] = args;
    if (String(url) === "/api/local-access") return reply(200, { request_token: REQUEST_TOKEN });
    const method = (options?.method || "GET").toUpperCase();
    if (!READS.has(method) && new Headers(options?.headers).get("X-SM64-Request-Token") !== REQUEST_TOKEN) {
      return reply(403, { detail: "Local request access denied." });
    }
    return fake(...args);
  });
  vi.stubGlobal("fetch", server);
  return server;
}
