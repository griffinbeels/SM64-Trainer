-- One server-owned row bounds account lookups without retaining unknown UIDs.
CREATE TABLE auth_entry_budget (
 id INTEGER PRIMARY KEY CHECK(id=1),
 day TEXT NOT NULL,
 ops INTEGER NOT NULL DEFAULT 0 CHECK(ops BETWEEN 0 AND 30000));
INSERT INTO auth_entry_budget(id,day,ops) VALUES(1,'',0);
