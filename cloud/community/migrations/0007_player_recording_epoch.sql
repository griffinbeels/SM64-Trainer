-- One monotonic owner fence for exact paged ghost inventories. It is private
-- metadata and never expands the strict v1 account response.
ALTER TABLE accounts ADD COLUMN recording_epoch INTEGER NOT NULL DEFAULT 0 CHECK(recording_epoch>=0);
