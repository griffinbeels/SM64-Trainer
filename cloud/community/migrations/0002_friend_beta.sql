-- One-use invitations are capabilities, never identity-provider or operator keys.
CREATE TABLE beta_invitations (
 token_sha256 TEXT PRIMARY KEY CHECK(length(token_sha256)=64),
 expires_at INTEGER NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)),
 claimed_uid TEXT UNIQUE REFERENCES accounts(uid), claimed_at INTEGER,
 created_at INTEGER NOT NULL);
CREATE TABLE beta_admission (
 id INTEGER PRIMARY KEY CHECK(id=1),
 max_members INTEGER NOT NULL DEFAULT 8 CHECK(max_members BETWEEN 1 AND 64));
INSERT INTO beta_admission(id,max_members) VALUES(1,8);
CREATE TABLE maintenance_quotas (
 day TEXT PRIMARY KEY, ops INTEGER NOT NULL DEFAULT 0 CHECK(ops BETWEEN 0 AND 10000),
 class_a INTEGER NOT NULL DEFAULT 0 CHECK(class_a BETWEEN 0 AND 6000),
 class_b INTEGER NOT NULL DEFAULT 0 CHECK(class_b BETWEEN 0 AND 6000));
CREATE INDEX fastest_strategy ON recordings(target_key,clock,rom,generation,strategy,frames,id);
CREATE INDEX fastest_overall ON recordings(target_key,clock,rom,generation,frames,id);
ALTER TABLE grants ADD COLUMN retry_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE assets ADD COLUMN retry_at INTEGER NOT NULL DEFAULT 0;
CREATE INDEX grants_retry ON grants(state,retry_at,expires_at);
CREATE INDEX assets_retry ON assets(retry_at,created_at,key);
