-- Which revision of each document or task a signed-in person last opened on the
-- website. One row per person and resource: identifiers, a revision number and a
-- timestamp, never content. The website compares seen_revision with the current
-- revision to show what agents changed since the person last looked. Rows follow
-- their resource when it is deleted.
CREATE TABLE space.resource_views (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    resource_id uuid NOT NULL,
    seen_revision integer NOT NULL CHECK (seen_revision > 0),
    seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, principal_id, resource_id),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id),
    FOREIGN KEY (organization_id, space_id, resource_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE
);
ALTER TABLE space.resource_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.resource_views FORCE ROW LEVEL SECURITY;
-- A person reads and writes only their own rows inside the requested Space.
CREATE POLICY own_views ON space.resource_views TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND principal_id=space.current_principal_id()
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND principal_id=space.current_principal_id()
);
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.resource_views TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
GRANT SELECT,INSERT,UPDATE ON space.resource_views TO space_app;
REVOKE ALL ON space.resource_views FROM PUBLIC;
