-- Autonomous memory brain: a real vector index beside the encrypted memory text.
-- There is no capture queue: an authorized agent decides what to remember and calls
-- remember or build_memory. The server stores what it is given.
-- Claim text stays encrypted inside Space resource revisions (see docs/MEMORY.md).
-- Readable here: the embedding, the embedding model name, the memory type, a
-- SHA-256 hash of the claim text, the SHA-256 cue hashes, the declared importance,
-- the originating source resource and revision, timestamps, and opaque identifiers.
-- No claim text is readable in this table.
--
-- The vector type must live in the space schema: the restricted runtime login
-- resolves types through search_path = pg_catalog, space.
SET LOCAL search_path = space, public;

CREATE EXTENSION IF NOT EXISTS vector SCHEMA space;

DO $extension$
BEGIN
    IF (SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'vector') IS DISTINCT FROM 'space' THEN
        RAISE EXCEPTION 'The vector extension must be installed in the space schema';
    END IF;
END
$extension$;

-- One row per recallable claim of the *current* revision of a memory resource.
-- Rebuilding a memory deletes and reinserts only that memory's rows.
-- embedding is vector(1536) for the selected text-embedding-3-small model.
-- Changing the embedding model or its dimensions needs a new migration.
CREATE TABLE space.memory_claims (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    entry_index integer NOT NULL CHECK (entry_index >= 0),
    memory_type text NOT NULL CHECK (memory_type IN ('factual', 'procedural', 'episodic')),
    text_hash bytea NOT NULL CHECK (octet_length(text_hash) = 32),
    cue_hashes bytea[] NOT NULL DEFAULT '{}',
    embedding vector(1536) NOT NULL,
    embedding_model text NOT NULL CHECK (length(embedding_model) BETWEEN 1 AND 200),
    source_resource_id uuid,
    source_revision integer CHECK (source_revision IS NULL OR source_revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- claim_id is stable across edits, so deleting one memory never disturbs
    -- the index rows of its neighbours.
    PRIMARY KEY (organization_id, space_id, memory_id, claim_id),
    FOREIGN KEY (organization_id, space_id, memory_id)
        REFERENCES space.resources(organization_id, space_id, id),
    CHECK ((source_resource_id IS NULL) = (source_revision IS NULL))
);

-- Approximate nearest-neighbour recall. Cosine distance matches the normalized
-- vectors returned by the model transport.
CREATE INDEX memory_claims_embedding_idx ON space.memory_claims
    USING hnsw (embedding vector_cosine_ops);
-- Cue expansion: find other current claims that share a generated cue.
CREATE INDEX memory_claims_cues_idx ON space.memory_claims
    USING gin (cue_hashes);

DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['memory_claims'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY private_owner ON space.%I TO space_app USING (
            organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id = %I.organization_id AND s.id = %I.space_id)
        ) WITH CHECK (
            organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id = %I.organization_id AND s.id = %I.space_id)
        )', t, t, t, t, t);
    END LOOP;
END
$policies$;

GRANT SELECT, INSERT, UPDATE, DELETE ON space.memory_claims TO space_app;
REVOKE ALL ON ALL TABLES IN SCHEMA space FROM PUBLIC;
