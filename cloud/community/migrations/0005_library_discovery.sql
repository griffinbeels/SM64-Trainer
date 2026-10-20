-- Summary identity groups follow this keyset. Existing fastest_strategy serves
-- the recording page. Do not duplicate its timing/index authority.
CREATE INDEX recording_library ON recordings(generation,target_key,clock,rom,strategy,frames,id) WHERE published=1;
