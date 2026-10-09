PRAGMA foreign_keys = ON;
CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK(id=1), generation TEXT NOT NULL, global_epoch INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, migration_generation TEXT, migration_cursor TEXT NOT NULL DEFAULT '');
INSERT INTO settings(id,generation) VALUES(1,'');
CREATE TABLE accounts (
 uid TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, invited INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','active','rename_required','suspended','deleted')),
 name TEXT, name_key TEXT UNIQUE, sharing INTEGER NOT NULL DEFAULT 0, terms_version TEXT,
 privacy_version TEXT, sheet_runner TEXT, sheet_verified INTEGER NOT NULL DEFAULT 0,
 bytes_used INTEGER NOT NULL DEFAULT 0 CHECK(bytes_used>=0), bytes_reserved INTEGER NOT NULL DEFAULT 0 CHECK(bytes_reserved>=0),
 pending INTEGER NOT NULL DEFAULT 0 CHECK(pending>=0), created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 UNIQUE(issuer,subject));
CREATE INDEX account_names ON accounts(name_key,uid);
CREATE TABLE scope_epochs (scope TEXT PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 0);
CREATE TABLE quotas (
 owner TEXT NOT NULL, day TEXT NOT NULL, ops_limit INTEGER NOT NULL, upload_limit INTEGER NOT NULL,
 ops INTEGER NOT NULL DEFAULT 0 CHECK(ops>=0 AND ops<=ops_limit),
 uploaded INTEGER NOT NULL DEFAULT 0 CHECK(uploaded>=0 AND uploaded<=upload_limit),
 class_a INTEGER NOT NULL DEFAULT 0 CHECK(class_a>=0 AND class_a<=20000),
 class_b INTEGER NOT NULL DEFAULT 0 CHECK(class_b>=0 AND class_b<=200000), PRIMARY KEY(owner,day));
CREATE INDEX quotas_expiry ON quotas(day,owner);
CREATE TABLE storage_budget (
 id INTEGER PRIMARY KEY CHECK(id=1), bytes_used INTEGER NOT NULL DEFAULT 0 CHECK(bytes_used>=0),
 bytes_reserved INTEGER NOT NULL DEFAULT 0 CHECK(bytes_reserved>=0));
INSERT INTO storage_budget(id) VALUES(1);
CREATE TABLE guards (id TEXT PRIMARY KEY, allowed INTEGER NOT NULL CHECK(allowed=1));
CREATE TABLE grants (
 id TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES accounts(uid), request_key TEXT NOT NULL, request_digest TEXT NOT NULL,
 target_key TEXT NOT NULL, target_json TEXT NOT NULL, clock TEXT NOT NULL, rom TEXT NOT NULL, strategy TEXT NOT NULL,
 frames INTEGER NOT NULL, raw_sha256 TEXT NOT NULL, raw_bytes INTEGER NOT NULL, encoded_sha256 TEXT NOT NULL,
 encoded_bytes INTEGER NOT NULL, generation TEXT NOT NULL, step INTEGER NOT NULL, overall_step INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('pending','receiving','recovering','stored','complete','failed')),
 multipart_id TEXT,
 expires_at INTEGER NOT NULL, asset_key TEXT NOT NULL UNIQUE, recording_id TEXT, disposition TEXT,
 created_at INTEGER NOT NULL, UNIQUE(uid,request_key));
CREATE INDEX grants_expiry ON grants(state,expires_at);
CREATE INDEX grants_terminal_expiry ON grants(state,created_at,id);
CREATE TABLE assets (
 key TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES accounts(uid), raw_sha256 TEXT NOT NULL,
 encoded_sha256 TEXT NOT NULL, encoded_bytes INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('stored','deleting')),
 created_at INTEGER NOT NULL);
CREATE TABLE recordings (
 id TEXT PRIMARY KEY, uid TEXT NOT NULL REFERENCES accounts(uid), asset_key TEXT NOT NULL REFERENCES assets(key),
 target_key TEXT NOT NULL, target_json TEXT NOT NULL, clock TEXT NOT NULL, rom TEXT NOT NULL, strategy TEXT NOT NULL,
 frames INTEGER NOT NULL, raw_sha256 TEXT NOT NULL, raw_bytes INTEGER NOT NULL, encoded_sha256 TEXT NOT NULL,
 encoded_bytes INTEGER NOT NULL, codec TEXT NOT NULL DEFAULT 'ghc1', generation TEXT NOT NULL,
 step INTEGER NOT NULL, overall_step INTEGER NOT NULL, published INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
CREATE INDEX pool_strategy ON recordings(target_key,clock,rom,generation,strategy,step,frames,id);
CREATE INDEX pool_overall ON recordings(target_key,clock,rom,generation,overall_step,frames,id);
CREATE INDEX recording_owner ON recordings(uid,published,id);
CREATE INDEX recording_search ON recordings(uid,target_key,clock,rom,generation,id);
CREATE TABLE winners (
 uid TEXT NOT NULL REFERENCES accounts(uid), target_key TEXT NOT NULL, clock TEXT NOT NULL, rom TEXT NOT NULL,
 strategy TEXT NOT NULL, generation TEXT NOT NULL, step INTEGER NOT NULL, recording_id TEXT NOT NULL REFERENCES recordings(id),
 frames INTEGER NOT NULL, PRIMARY KEY(uid,target_key,clock,rom,strategy,generation,step));
CREATE INDEX winner_recording ON winners(recording_id);
CREATE TABLE pins (
 uid TEXT NOT NULL REFERENCES accounts(uid), recording_id TEXT NOT NULL REFERENCES recordings(id),
 expires_at INTEGER NOT NULL, PRIMARY KEY(uid,recording_id));
CREATE INDEX pins_expiry ON pins(expires_at,recording_id);
CREATE TABLE mutation_receipts (
 uid TEXT NOT NULL, route TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,
 response_json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(uid,route,request_key));
CREATE INDEX receipts_expiry ON mutation_receipts(created_at,uid,route,request_key);
CREATE TABLE admin_audit (
 id TEXT PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL,
 reason TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE deletion_outbox (uid TEXT PRIMARY KEY REFERENCES accounts(uid), state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','retrying','complete','operator_pending')), attempts INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL DEFAULT 0, last_error TEXT, completed_at INTEGER, created_at INTEGER NOT NULL);
CREATE INDEX deletion_retry ON deletion_outbox(state,next_at,uid);
