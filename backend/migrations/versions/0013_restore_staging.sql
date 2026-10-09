CREATE TABLE space.restore_sessions (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    key_id uuid NOT NULL,
    encrypted_manifest bytea NOT NULL CHECK (octet_length(encrypted_manifest) BETWEEN 1 AND 10000),
    encrypted_result bytea,
    state text NOT NULL DEFAULT 'staged' CHECK (state IN ('staged','publishing','committed')),
    lease_id uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '24 hours',
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id) REFERENCES space.spaces(organization_id,id),
    FOREIGN KEY (organization_id,space_id,key_id) REFERENCES space.space_keys(organization_id,space_id,id)
);
CREATE TABLE space.restore_chunks (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    restore_id uuid NOT NULL,
    part integer NOT NULL CHECK (part BETWEEN 0 AND 1023),
    encrypted_payload bytea NOT NULL CHECK (octet_length(encrypted_payload) BETWEEN 1 AND 1500000),
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint)=32),
    plain_bytes integer NOT NULL CHECK (plain_bytes BETWEEN 1 AND 720000),
    PRIMARY KEY (organization_id,space_id,restore_id,part),
    FOREIGN KEY (organization_id,space_id,restore_id) REFERENCES space.restore_sessions(organization_id,space_id,id) ON DELETE CASCADE
);
-- Upload intents survive a process crash. Cleanup waits for the publication lease to expire.
CREATE TABLE space.restore_objects (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    restore_id uuid NOT NULL,
    object_key text NOT NULL,
    lease_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,object_key)
);
DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['restore_sessions','restore_chunks','restore_objects'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY private_owner ON space.%I TO space_app USING (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=%I.organization_id AND s.id=%I.space_id)
        ) WITH CHECK (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=%I.organization_id AND s.id=%I.space_id)
        )',t,t,t,t,t);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I USING (true) WITH CHECK (true)',t,current_user);
        EXECUTE format('REVOKE ALL ON space.%I FROM PUBLIC',t);
        EXECUTE format('GRANT SELECT,INSERT,DELETE ON space.%I TO space_app',t);
    END LOOP;
END
$policies$;
GRANT UPDATE (state,lease_id,lease_until,encrypted_result) ON space.restore_sessions TO space_app;
