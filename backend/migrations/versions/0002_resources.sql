CREATE TABLE space.collections (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    system_role text NOT NULL CHECK (system_role = 'tasks'),
    key_id uuid NOT NULL,
    encrypted_schema bytea NOT NULL CHECK (octet_length(encrypted_schema) BETWEEN 1 AND 1500000),
    PRIMARY KEY (organization_id, space_id, id),
    UNIQUE (organization_id, space_id, system_role),
    FOREIGN KEY (organization_id, space_id, key_id) REFERENCES space.space_keys(organization_id, space_id, id)
);

CREATE TABLE space.resources (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('document', 'record')),
    collection_id uuid,
    current_revision integer NOT NULL CHECK (current_revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, id),
    CHECK ((kind = 'document' AND collection_id IS NULL) OR (kind = 'record' AND collection_id IS NOT NULL)),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id),
    FOREIGN KEY (organization_id, space_id, collection_id) REFERENCES space.collections(organization_id, space_id, id)
);

CREATE TABLE space.resource_revisions (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    key_id uuid NOT NULL,
    encrypted_metadata bytea NOT NULL CHECK (octet_length(encrypted_metadata) BETWEEN 1 AND 1500000),
    encrypted_record bytea CHECK (octet_length(encrypted_record) BETWEEN 1 AND 1500000),
    object_key text CHECK (length(object_key) BETWEEN 1 AND 512),
    author_principal_id uuid NOT NULL REFERENCES space.principals(id),
    author_client_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, resource_id, revision),
    CHECK ((encrypted_record IS NULL) <> (object_key IS NULL)),
    FOREIGN KEY (organization_id, space_id, resource_id) REFERENCES space.resources(organization_id, space_id, id),
    FOREIGN KEY (organization_id, space_id, key_id) REFERENCES space.space_keys(organization_id, space_id, id)
);
ALTER TABLE space.resources ADD CONSTRAINT resource_current_revision_fk
    FOREIGN KEY (organization_id, space_id, id, current_revision)
    REFERENCES space.resource_revisions(organization_id, space_id, resource_id, revision)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE space.commands (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    client_id uuid,
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint) = 32),
    resource_id uuid NOT NULL,
    revision integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, id),
    FOREIGN KEY (organization_id, space_id, resource_id, revision)
        REFERENCES space.resource_revisions(organization_id, space_id, resource_id, revision)
);

CREATE TABLE space.events (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    command_id uuid NOT NULL,
    event_type text NOT NULL CHECK (event_type IN ('resource.created', 'resource.updated')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, id),
    UNIQUE (organization_id, space_id, command_id),
    FOREIGN KEY (organization_id, space_id, command_id) REFERENCES space.commands(organization_id, space_id, id)
);

CREATE TABLE space.oauth_grants (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    client_id uuid NOT NULL,
    id uuid NOT NULL UNIQUE,
    resource text NOT NULL CHECK (length(resource) BETWEEN 1 AND 2048),
    can_write boolean NOT NULL DEFAULT false,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, client_id),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id)
);

DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['collections', 'resources', 'resource_revisions', 'commands', 'events', 'oauth_grants'] LOOP
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

GRANT SELECT, INSERT ON space.collections, space.resources, space.resource_revisions,
    space.commands, space.events, space.oauth_grants TO space_app;
GRANT UPDATE (current_revision) ON space.resources TO space_app;
GRANT UPDATE (id, resource, can_write, revoked_at, created_at) ON space.oauth_grants TO space_app;
REVOKE ALL ON ALL TABLES IN SCHEMA space FROM PUBLIC;
