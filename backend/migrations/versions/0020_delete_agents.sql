ALTER TABLE space.agent_identities ADD COLUMN deleted_at timestamptz;
GRANT UPDATE (deleted_at) ON space.agent_identities TO space_app;
ALTER TABLE space.agent_identities DROP CONSTRAINT agent_identities_organization_id_space_id_identity_hash_key;
CREATE UNIQUE INDEX agent_identities_active_identity_hash
    ON space.agent_identities (organization_id, space_id, identity_hash)
    WHERE deleted_at IS NULL;
