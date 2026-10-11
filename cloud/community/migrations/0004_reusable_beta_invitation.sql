-- Existing installations retain single-use expiry and member-cap behavior.
-- Reusable expiry zero stays valid until an operator explicitly revokes it.
ALTER TABLE beta_invitations ADD COLUMN reusable INTEGER NOT NULL DEFAULT 0 CHECK(reusable IN (0,1));
ALTER TABLE beta_admission ADD COLUMN uncapped INTEGER NOT NULL DEFAULT 0 CHECK(uncapped IN (0,1));
