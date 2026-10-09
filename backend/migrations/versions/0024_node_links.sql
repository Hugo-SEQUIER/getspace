-- Explicit links connect whole memory nodes, independently of claim index rebuilds.
CREATE TABLE space.memory_node_links (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    source_memory_id uuid NOT NULL,
    target_memory_id uuid NOT NULL,
    relation text NOT NULL CHECK (relation IN ('related', 'expands_on')),
    PRIMARY KEY (organization_id, space_id, source_memory_id, target_memory_id, relation),
    CHECK (source_memory_id <> target_memory_id),
    FOREIGN KEY (organization_id, space_id, source_memory_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, space_id, target_memory_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE
);
CREATE INDEX memory_node_links_target_idx ON space.memory_node_links
    (organization_id, space_id, target_memory_id, source_memory_id);
ALTER TABLE space.memory_node_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.memory_node_links FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.memory_node_links TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_node_links.organization_id
                  AND s.id = memory_node_links.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_node_links.organization_id
                  AND s.id = memory_node_links.space_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON space.memory_node_links TO space_app;
REVOKE ALL ON space.memory_node_links FROM PUBLIC;

-- Never backfill historical links: missing hashes retain conservative revision pins.
ALTER TABLE space.memory_links
    ADD COLUMN relation text NOT NULL DEFAULT 'related'
        CHECK (relation IN ('related', 'expands_on')),
    ADD COLUMN source_text_hash bytea CHECK (octet_length(source_text_hash) = 32),
    ADD COLUMN target_text_hash bytea CHECK (octet_length(target_text_hash) = 32),
    ADD CONSTRAINT memory_links_hash_pair CHECK (
        (source_text_hash IS NULL) = (target_text_hash IS NULL)
    );

-- brain.apply prunes changed/forgotten claims explicitly. Resource FKs preserve
-- incoming edges during DELETE/INSERT reembedding of unchanged claims.
DO $constraints$
DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN SELECT conname FROM pg_constraint
        WHERE conrelid = 'space.memory_links'::regclass
          AND confrelid = 'space.memory_claims'::regclass AND contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE space.memory_links DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END;
$constraints$;
ALTER TABLE space.memory_links
    ADD CONSTRAINT memory_links_source_resource_fk
        FOREIGN KEY (organization_id, space_id, source_memory_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE,
    ADD CONSTRAINT memory_links_target_resource_fk
        FOREIGN KEY (organization_id, space_id, target_memory_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE;
