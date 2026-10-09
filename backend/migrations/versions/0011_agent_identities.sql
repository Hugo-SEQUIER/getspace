CREATE TABLE space.agent_identities (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    key_id uuid NOT NULL,
    encrypted_metadata bytea NOT NULL CHECK (octet_length(encrypted_metadata) BETWEEN 1 AND 16384),
    identity_hash bytea NOT NULL CHECK (octet_length(identity_hash) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, id),
    UNIQUE (organization_id, space_id, identity_hash),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id),
    FOREIGN KEY (organization_id, space_id, key_id) REFERENCES space.space_keys(organization_id, space_id, id)
);
ALTER TABLE space.agent_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.agent_identities FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.agent_identities TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=agent_identities.organization_id AND s.id=agent_identities.space_id)
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=agent_identities.organization_id AND s.id=agent_identities.space_id)
);
DO $maintenance$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.agent_identities TO %I USING (true) WITH CHECK (true)', current_user);
END
$maintenance$;
REVOKE ALL ON space.agent_identities FROM PUBLIC;
GRANT SELECT, INSERT ON space.agent_identities TO space_app;
GRANT UPDATE (revision,key_id,encrypted_metadata,identity_hash,updated_at) ON space.agent_identities TO space_app;

-- Existing connections stay unidentified until explicitly assigned. Never infer
-- identity from OAuth client names or manufacture one profile per connection.
ALTER TABLE space.oauth_grants ADD COLUMN agent_id uuid;
ALTER TABLE space.oauth_grants ADD CONSTRAINT oauth_grant_agent_fk
    FOREIGN KEY (organization_id,space_id,agent_id) REFERENCES space.agent_identities(organization_id,space_id,id);
GRANT UPDATE (agent_id) ON space.oauth_grants TO space_app;

-- The submitting identity is part of the durable actor receipt. Connection
-- reassignment must not silently attribute a queued job to another agent.
ALTER TABLE space.memory_jobs ADD COLUMN agent_id uuid;
ALTER TABLE space.memory_jobs ADD CONSTRAINT memory_job_agent_fk
    FOREIGN KEY (organization_id,space_id,agent_id) REFERENCES space.agent_identities(organization_id,space_id,id);
