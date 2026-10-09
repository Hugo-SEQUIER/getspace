-- Declared importance on an indexed claim.
--
-- The claim index has always ranked by vector similarity alone. Agents can now say
-- how much a claim matters when they write it, which is a signal they hold and the
-- retriever cannot infer. It is stored as a small readable integer next to the
-- embedding, for the same reason as the embedding: ranking needs it in SQL, and it
-- is a non-secret scalar. docs/PRIVACY.md records that it is readable.
--
-- 0 is the default and means "not declared". Recall behaviour is unchanged until a
-- measured ranking weight uses this column.
SET LOCAL search_path = space, public;

ALTER TABLE space.memory_claims
    ADD COLUMN importance smallint NOT NULL DEFAULT 0
    CHECK (importance BETWEEN 0 AND 3);
