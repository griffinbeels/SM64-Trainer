import {requireThat} from "./errors.js";
export const LIMITS = Object.freeze({
  encoded_bytes: 2 * 1024 * 1024, raw_bytes: 16 * 1024 * 1024,
  retained_bytes: 64 * 1024 * 1024, global_bytes: 10 * 1000 * 1000 * 1000,
  upload_bytes_day: 24 * 1024 * 1024, global_upload_bytes_day: 256 * 1024 * 1024,
  account_ops_day: 500, global_ops_day: 30000, pending_uploads: 2,
  grant_seconds: 300, pin_seconds: 7 * 86400, page_rows: 256, search_rows: 20, max_band: 45
});
export function config(env) {
  const supabase = new URL(env.SUPABASE_URL);
  requireThat(supabase.protocol === "https:" && !supabase.username && !supabase.password &&
    supabase.pathname === "/" && !supabase.search && !supabase.hash, "configuration", "Service is unavailable.",503);
  return {
    supabaseURL: supabase.origin, issuer: supabase.origin + "/auth/v1",
    jwks: supabase.origin + "/auth/v1/.well-known/jwks.json",
    clientID: env.OAUTH_CLIENT_ID, callback: env.NATIVE_CALLBACK,
    publishableKey: env.SUPABASE_PUBLISHABLE_KEY, terms: env.TERMS_VERSION, privacy: env.PRIVACY_VERSION,
    enabled: env.SERVICE_ENABLED === "true", operator: env.OPERATOR_NAME, contact: env.OPERATOR_CONTACT, policyURL: env.POLICY_URL,
    identityAdminConfigured:typeof env.SUPABASE_SECRET_KEY==='string'&&env.SUPABASE_SECRET_KEY.length>=16&&!env.SUPABASE_SECRET_KEY.startsWith('REPLACE'),
    paginationConfigured:typeof env.CURSOR_SECRET==='string'&&env.CURSOR_SECRET.length>=32
  };
}
export function ready(cfg) {
  requireThat(cfg.enabled && cfg.operator?.trim() && cfg.contact?.trim() && cfg.policyURL?.startsWith("https://") &&
    cfg.clientID && !cfg.clientID.startsWith("REPLACE") && cfg.publishableKey && !cfg.publishableKey.startsWith("REPLACE")&&cfg.identityAdminConfigured&&cfg.paginationConfigured&&typeof cfg.terms==='string'&&cfg.terms.trim()&&typeof cfg.privacy==='string'&&cfg.privacy.trim(),
    "service_disabled", "The private beta is not configured.",503);
  requireThat(cfg.callback==="http://127.0.0.1:8072/api/community/auth/callback","configuration","The beta callback is not registered.",503);
}
