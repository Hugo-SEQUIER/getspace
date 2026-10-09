-- No documents, names or settings are stored in plaintext in this foundation.
-- The API must verify external identity before setting transaction-local context.
CREATE TABLE space.principals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
    subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 512),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE space.organizations (
    id uuid PRIMARY KEY,
    owner_principal_id uuid NOT NULL UNIQUE REFERENCES space.principals(id),
    kind text NOT NULL DEFAULT 'personal' CHECK (kind = 'personal'),
    default_space_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE space.spaces (
    organization_id uuid NOT NULL REFERENCES space.organizations(id),
    id uuid NOT NULL,
    metadata_key_id uuid NOT NULL,
    encrypted_metadata bytea NOT NULL CHECK (octet_length(encrypted_metadata) BETWEEN 1 AND 1500000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, id)
);

CREATE TABLE space.space_keys (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    wrapping_provider text NOT NULL CHECK (length(wrapping_provider) BETWEEN 1 AND 100),
    wrapping_key_reference text NOT NULL CHECK (length(wrapping_key_reference) BETWEEN 1 AND 2048),
    wrapped_key bytea NOT NULL CHECK (octet_length(wrapped_key) BETWEEN 1 AND 65536),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, id),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id)
        DEFERRABLE INITIALLY DEFERRED
);

-- Bootstrap commits the organization, default Space and wrapped key together.
ALTER TABLE space.organizations ADD CONSTRAINT organizations_default_space_fk
    FOREIGN KEY (id, default_space_id) REFERENCES space.spaces(organization_id, id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE space.spaces ADD CONSTRAINT spaces_metadata_key_fk
    FOREIGN KEY (organization_id, id, metadata_key_id)
    REFERENCES space.space_keys(organization_id, space_id, id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE FUNCTION space.current_principal_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
    AS $$
        SELECT id FROM space.principals
        WHERE issuer = nullif(current_setting('space.identity_issuer', true), '')
          AND subject = nullif(current_setting('space.identity_subject', true), '')
    $$;

CREATE FUNCTION space.requested_organization_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
    AS $$ SELECT nullif(current_setting('space.organization_id', true), '')::uuid $$;

CREATE FUNCTION space.requested_space_id() RETURNS uuid
    LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pg_catalog
    AS $$ SELECT nullif(current_setting('space.space_id', true), '')::uuid $$;

ALTER TABLE space.principals ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.principals FORCE ROW LEVEL SECURITY;
CREATE POLICY principal_identity ON space.principals TO space_app
    USING (
        issuer = nullif(current_setting('space.identity_issuer', true), '')
        AND subject = nullif(current_setting('space.identity_subject', true), '')
    )
    WITH CHECK (
        issuer = nullif(current_setting('space.identity_issuer', true), '')
        AND subject = nullif(current_setting('space.identity_subject', true), '')
    );

ALTER TABLE space.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY personal_owner ON space.organizations TO space_app
    USING (owner_principal_id = space.current_principal_id())
    WITH CHECK (owner_principal_id = space.current_principal_id());

ALTER TABLE space.spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.spaces FORCE ROW LEVEL SECURITY;
CREATE POLICY private_space_owner ON space.spaces TO space_app
    USING (
        organization_id = space.requested_organization_id()
        AND id = space.requested_space_id()
        AND EXISTS (
            SELECT 1 FROM space.organizations o
            WHERE o.id = spaces.organization_id AND o.owner_principal_id = space.current_principal_id()
        )
    )
    WITH CHECK (
        organization_id = space.requested_organization_id()
        AND id = space.requested_space_id()
        AND EXISTS (
            SELECT 1 FROM space.organizations o
            WHERE o.id = spaces.organization_id AND o.owner_principal_id = space.current_principal_id()
        )
    );

ALTER TABLE space.space_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.space_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY private_space_keys ON space.space_keys TO space_app
    USING (
        organization_id = space.requested_organization_id()
        AND space_id = space.requested_space_id()
        AND EXISTS (
            SELECT 1 FROM space.spaces s
            WHERE s.organization_id = space_keys.organization_id AND s.id = space_keys.space_id
        )
    )
    WITH CHECK (
        organization_id = space.requested_organization_id()
        AND space_id = space.requested_space_id()
        AND EXISTS (
            SELECT 1 FROM space.spaces s
            WHERE s.organization_id = space_keys.organization_id AND s.id = space_keys.space_id
        )
    );

REVOKE ALL ON ALL TABLES IN SCHEMA space FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA space FROM PUBLIC;
GRANT SELECT, INSERT ON space.principals, space.organizations, space.spaces, space.space_keys TO space_app;
GRANT UPDATE (encrypted_metadata, metadata_key_id) ON space.spaces TO space_app;
GRANT EXECUTE ON FUNCTION space.current_principal_id(), space.requested_organization_id(),
    space.requested_space_id() TO space_app;
