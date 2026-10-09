-- Derived navigation only: claim text, confidence and status stay encrypted.
-- The primary key indexes outgoing links; the second index serves backlinks.
CREATE TABLE space.memory_links (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    source_memory_id uuid NOT NULL,
    source_claim_id uuid NOT NULL,
    source_revision integer NOT NULL CHECK (source_revision > 0),
    target_memory_id uuid NOT NULL,
    target_claim_id uuid NOT NULL,
    target_revision integer NOT NULL CHECK (target_revision > 0),
    PRIMARY KEY (organization_id, space_id, source_memory_id, source_claim_id,
                 target_memory_id, target_claim_id),
    FOREIGN KEY (organization_id, space_id, source_memory_id, source_claim_id)
        REFERENCES space.memory_claims(organization_id, space_id, memory_id, claim_id)
        ON DELETE CASCADE,
    FOREIGN KEY (organization_id, space_id, target_memory_id, target_claim_id)
        REFERENCES space.memory_claims(organization_id, space_id, memory_id, claim_id)
        ON DELETE CASCADE
);

CREATE INDEX memory_links_target_idx ON space.memory_links
    (organization_id, space_id, target_memory_id, target_claim_id,
     source_memory_id, source_claim_id);

ALTER TABLE space.memory_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.memory_links FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.memory_links TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_links.organization_id
                  AND s.id = memory_links.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = memory_links.organization_id
                  AND s.id = memory_links.space_id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON space.memory_links TO space_app;
REVOKE ALL ON space.memory_links FROM PUBLIC;
