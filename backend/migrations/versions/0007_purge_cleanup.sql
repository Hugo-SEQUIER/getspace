-- Operator-owned cleanup survives the deletion of its Space and keys. There are
-- intentionally no foreign keys to the data being purged, and no runtime grants.
CREATE TABLE space.purge_cleanup (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    object_key text NOT NULL,
    is_prefix boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, object_key)
);
ALTER TABLE space.purge_cleanup ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON space.purge_cleanup FROM PUBLIC, space_app;

-- FORCE RLS also applies to non-superuser schema owners. Give the migration
-- owner its intended maintenance access without granting it to the runtime.
DO $maintenance$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['principals', 'organizations', 'spaces',
        'space_keys', 'collections', 'resources', 'resource_revisions', 'commands',
        'events', 'oauth_grants', 'memory_claims', 'resource_terms'] LOOP
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I '
            'USING (true) WITH CHECK (true)', table_name, current_user);
    END LOOP;
END
$maintenance$;
