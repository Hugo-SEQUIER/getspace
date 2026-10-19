-- One profile picture per person, chosen on the website. The picture is stored here as a
-- readable payload, like every other payload of this edition, beside the Space key record
-- and revision that name it. Nobody else reads it: the row is visible only to its own person
-- inside that Space, and it is purged with the Space.
CREATE TABLE space.profile_images (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    key_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    encrypted_image bytea NOT NULL CHECK (octet_length(encrypted_image) BETWEEN 1 AND 600000),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, principal_id),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id),
    FOREIGN KEY (organization_id, space_id, key_id)
        REFERENCES space.space_keys(organization_id, space_id, id)
);
ALTER TABLE space.profile_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.profile_images FORCE ROW LEVEL SECURITY;
-- A person reads, replaces and removes only their own picture inside the requested Space.
CREATE POLICY own_image ON space.profile_images TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND principal_id=space.current_principal_id()
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND principal_id=space.current_principal_id()
);
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.profile_images TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
GRANT SELECT,INSERT,UPDATE,DELETE ON space.profile_images TO space_app;
REVOKE ALL ON space.profile_images FROM PUBLIC;
