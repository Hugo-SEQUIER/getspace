-- Parameters: the file-part ledger from `0041_document_files`.
-- What it does: moves a part's bytes out of the row and into an object on the server's
-- disk, named by `object_key`. PostgreSQL keeps the path, the sizes and the digest, so one
-- large upload no longer grows the database, its write-ahead log or its dumps. A part
-- written before this migration keeps its `content`; every new part stores a key.
-- Output: `content` and `object_key` alternate: exactly one of them is set.
ALTER TABLE space.file_parts ALTER COLUMN content DROP NOT NULL;
ALTER TABLE space.file_parts ADD COLUMN object_key text;
-- A row names its bytes once: inline for a part from before this migration, by key after
-- it. Neither both nor neither can be read safely.
ALTER TABLE space.file_parts ADD CONSTRAINT file_parts_stored_once CHECK (
    (content IS NULL) <> (object_key IS NULL)
);
