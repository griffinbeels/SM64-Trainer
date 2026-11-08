-- A ghost's start: optional opaque bytes stored in the same R2 object, after
-- the ghost's encoded bytes. Zero bytes and no hash mean none, which is every
-- existing row and every upload from a trainer that sends no start. The cap
-- lives in config.js so it can change without rebuilding these tables.
ALTER TABLE grants ADD COLUMN start_sha256 TEXT CHECK(start_sha256 IS NULL OR length(start_sha256)=64);
ALTER TABLE grants ADD COLUMN start_bytes INTEGER NOT NULL DEFAULT 0 CHECK(start_bytes>=0 AND (start_bytes=0)=(start_sha256 IS NULL));
ALTER TABLE assets ADD COLUMN start_sha256 TEXT CHECK(start_sha256 IS NULL OR length(start_sha256)=64);
ALTER TABLE assets ADD COLUMN start_bytes INTEGER NOT NULL DEFAULT 0 CHECK(start_bytes>=0 AND (start_bytes=0)=(start_sha256 IS NULL));
