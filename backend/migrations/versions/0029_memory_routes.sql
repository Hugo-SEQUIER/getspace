-- Routing rules are memory claims linked to the containers they govern. Only opaque
-- identifiers are readable; claim text and target names stay encrypted.
CREATE TABLE space.memory_routes (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    -- NULL marks a Space-wide rule that governs no single container.
    target_item_id uuid,
    origin text NOT NULL CHECK (origin IN ('explicit', 'inferred')),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (organization_id, space_id, memory_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, space_id, target_item_id)
        REFERENCES space.workspace_items(organization_id, space_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX memory_routes_claim_target_idx ON space.memory_routes
    (organization_id, space_id, claim_id, target_item_id) NULLS NOT DISTINCT;
CREATE INDEX memory_routes_target_idx ON space.memory_routes
    (organization_id, space_id, target_item_id, created_at DESC);
CREATE INDEX memory_routes_memory_idx ON space.memory_routes
    (organization_id, space_id, memory_id);
ALTER TABLE space.memory_routes ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.memory_routes FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.memory_routes TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_routes.organization_id
                  AND s.id = memory_routes.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_routes.organization_id
                  AND s.id = memory_routes.space_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE ON space.memory_routes TO space_app;
REVOKE ALL ON space.memory_routes FROM PUBLIC;
