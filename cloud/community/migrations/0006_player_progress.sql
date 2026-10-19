-- Separate current-state progress publication from ghost sharing and v1 accounts.
CREATE TABLE progress_publications (
 uid TEXT PRIMARY KEY REFERENCES accounts(uid), revision INTEGER NOT NULL DEFAULT 0,
 consent_revision INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 visible INTEGER NOT NULL DEFAULT 0 CHECK(visible IN (0,1)), publisher_json TEXT,
 notice_version TEXT, changed_utc TEXT NOT NULL);
CREATE TABLE progress_snapshots (
 uid TEXT PRIMARY KEY REFERENCES progress_publications(uid), content_json TEXT NOT NULL,
 content_hash TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK(byte_size>=0 AND byte_size<=1048576),
 captured_utc TEXT NOT NULL, synced_utc TEXT NOT NULL);
CREATE TABLE progress_storage_budget (id INTEGER PRIMARY KEY CHECK(id=1),bytes_used INTEGER NOT NULL DEFAULT 0 CHECK(bytes_used>=0));
INSERT INTO progress_storage_budget(id) VALUES(1);
CREATE TABLE player_profiles (
 uid TEXT PRIMARY KEY REFERENCES accounts(uid), revision INTEGER NOT NULL,
 bio TEXT NOT NULL DEFAULT '', socials_json TEXT NOT NULL DEFAULT '[]', updated_utc TEXT NOT NULL,
 byte_size INTEGER NOT NULL DEFAULT 0 CHECK(byte_size>=0 AND byte_size<=16384));
-- Revocation/moderation fences an already admitted upload, even if the account
-- is restored before it arrives. Existing v1 response schemas remain unchanged.
CREATE TRIGGER progress_moderation_fence AFTER UPDATE OF invited,status ON accounts
WHEN (OLD.invited=1 AND NEW.invited=0) OR
 (NEW.status IN ('suspended','deleted') AND OLD.status!=NEW.status)
BEGIN
 UPDATE progress_publications SET revision=revision+1,consent_revision=consent_revision+1,
 enabled=0,visible=0 WHERE uid=NEW.uid;
END;
