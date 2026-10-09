-- Encrypted catalog embeddings for shortlisting. Only identifiers are readable;
-- vectors are ranked in process after decryption, never in SQL.
CREATE TABLE space.workspace_item_vectors (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    item_id uuid NOT NULL,
    item_revision integer NOT NULL CHECK (item_revision > 0),
    embedding_model text NOT NULL CHECK (length(embedding_model) BETWEEN 1 AND 200),
    key_id uuid NOT NULL,
    encrypted_vector bytea NOT NULL CHECK (octet_length(encrypted_vector) BETWEEN 1 AND 100000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, item_id, embedding_model),
    FOREIGN KEY (organization_id, space_id, item_id, item_revision)
        REFERENCES space.workspace_item_revisions(organization_id, space_id, item_id, revision),
    FOREIGN KEY (organization_id, space_id, key_id)
        REFERENCES space.space_keys(organization_id, space_id, id)
);
ALTER TABLE space.workspace_item_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.workspace_item_vectors FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.workspace_item_vectors TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = workspace_item_vectors.organization_id
                  AND s.id = workspace_item_vectors.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = workspace_item_vectors.organization_id
                  AND s.id = workspace_item_vectors.space_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON space.workspace_item_vectors TO space_app;
REVOKE ALL ON space.workspace_item_vectors FROM PUBLIC;

-- One automatic catch-up per Space reclassifies claims the old 32-item limit left
-- unassigned. It runs under the grant of the memory write that queued it.
ALTER TABLE space.memory_jobs DROP CONSTRAINT memory_jobs_request_kind_check;
ALTER TABLE space.memory_jobs ADD CONSTRAINT memory_jobs_request_kind_check
    CHECK (request_kind IN ('remember','build','memora_import','reclassify'));
ALTER TABLE space.memory_jobs DROP CONSTRAINT memory_jobs_import_count_check;
ALTER TABLE space.memory_jobs ADD CONSTRAINT memory_jobs_import_count_check
    CHECK ((request_kind IN ('memora_import','reclassify'))=(import_total>0));
