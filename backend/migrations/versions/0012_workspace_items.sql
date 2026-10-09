ALTER TABLE space.spaces ADD COLUMN content_revision bigint NOT NULL DEFAULT 0;
GRANT UPDATE (content_revision) ON space.spaces TO space_app;

CREATE TABLE space.workspace_items (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('project','database')),
    current_revision integer NOT NULL CHECK (current_revision > 0),
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id) REFERENCES space.spaces(organization_id,id)
);
CREATE TABLE space.workspace_item_revisions (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    item_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    key_id uuid NOT NULL,
    encrypted_payload bytea NOT NULL CHECK (octet_length(encrypted_payload) BETWEEN 1 AND 1500000),
    deleted boolean NOT NULL DEFAULT false,
    author_principal_id uuid NOT NULL REFERENCES space.principals(id),
    author_client_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,item_id,revision),
    FOREIGN KEY (organization_id,space_id,item_id) REFERENCES space.workspace_items(organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,key_id) REFERENCES space.space_keys(organization_id,space_id,id)
);
ALTER TABLE space.workspace_items ADD CONSTRAINT workspace_item_current_revision_fk
    FOREIGN KEY (organization_id,space_id,id,current_revision)
    REFERENCES space.workspace_item_revisions(organization_id,space_id,item_id,revision)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE space.workspace_item_commands (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    item_id uuid NOT NULL,
    revision integer NOT NULL,
    client_id uuid,
    agent_id uuid,
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint)=32),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,item_id,revision)
        REFERENCES space.workspace_item_revisions(organization_id,space_id,item_id,revision)
);
DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['workspace_items','workspace_item_revisions','workspace_item_commands'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY private_owner ON space.%I TO space_app USING (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=%I.organization_id AND s.id=%I.space_id)
        ) WITH CHECK (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=%I.organization_id AND s.id=%I.space_id)
        )', t,t,t,t,t);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I USING (true) WITH CHECK (true)', t,current_user);
        EXECUTE format('REVOKE ALL ON space.%I FROM PUBLIC', t);
        EXECUTE format('GRANT SELECT,INSERT ON space.%I TO space_app', t);
    END LOOP;
END
$policies$;
GRANT UPDATE (current_revision,deleted) ON space.workspace_items TO space_app;
