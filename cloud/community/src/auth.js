import {createRemoteJWKSet, jwtVerify} from "jose";
import {requireThat, fail, uuid} from "./errors.js";
const keysets = new Map();
function remote(url) {
  if (!keysets.has(url)) { if (keysets.size > 4) keysets.clear();
    keysets.set(url, createRemoteJWKSet(new URL(url), {timeoutDuration: 5000, cooldownDuration: 30000, cacheMaxAge: 600000}));
  }
  return keysets.get(url);
}
export async function verifyUserToken(token, cfg, keys = remote(cfg.jwks)) {
  requireThat(typeof token === "string" && token.length <= 8192, "invalid_token", "Sign in again.",401);
  let payload;
  try { ({payload} = await jwtVerify(token, keys, {issuer: cfg.issuer, audience: "authenticated",
    algorithms: ["ES256","RS256"], requiredClaims: ["sub","exp","iat","client_id"], clockTolerance: 0})); }
  catch { fail(401, "invalid_token", "Sign in again."); }
  requireThat(uuid(payload.sub) && (cfg.nativeClients || [{client_id:cfg.clientID}]).some(pair=>pair.client_id===payload.client_id) &&
    payload.role === "authenticated" && payload.aud === "authenticated" &&
    Number.isFinite(payload.iat) && payload.iat <= Date.now()/1000 + 30, "invalid_token", "Sign in again.",401);
  return {sub: payload.sub.toLowerCase(), issuer: cfg.issuer};
}
export async function authenticate(request, cfg) {
  const header = request.headers.get("authorization");
  requireThat(header?.startsWith("Bearer ") && !header.includes(","), "authentication_required", "Sign in to continue.",401);
  return verifyUserToken(header.slice(7), cfg);
}
export async function authenticateAdmin(request, env, keys) {
  const issuer = env.ACCESS_ISSUER, aud = env.ACCESS_AUD;
  requireThat(issuer?.startsWith("https://") && aud && !aud.startsWith("REPLACE"), "admin_disabled","Administration is unavailable.",503);
  const token = request.headers.get("cf-access-jwt-assertion");
  requireThat(token && token.length <= 8192, "admin_required","Administrator access required.",403);
  let payload;
  try { ({payload} = await jwtVerify(token, keys || remote(issuer + "/cdn-cgi/access/certs"),
    {issuer, audience: aud, algorithms:["RS256"], requiredClaims:["sub","exp","iat","email"]})); }
  catch { fail(403, "admin_required","Administrator access required."); }
  const allowed = (env.ADMIN_EMAILS || "").split(",").map(s=>s.trim().toLowerCase()).filter(Boolean);
  requireThat(typeof payload.email === "string" && allowed.includes(payload.email.toLowerCase()),
    "admin_required","Administrator access required.",403);
  return payload.email.toLowerCase();
}
