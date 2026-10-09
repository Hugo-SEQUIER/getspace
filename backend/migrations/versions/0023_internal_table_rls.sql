-- Parameters: Existing Space schema; execute as its trusted migration/table owner.
-- What it does: Closes the four internal-table RLS gaps without changing workspace policies.
-- Output: Runtime reads of environment status remain allowed; other direct reads default-deny.
ALTER TABLE space.alembic_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.environment_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.erased_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.deletion_receipts ENABLE ROW LEVEL SECURITY;

-- Parameters: The existing read-only space_app grant on environment_control.
-- What it does: Preserves that non-tenant, singleton status read. Adds no write privileges.
-- Output: Idempotent SELECT policy; trusted owners retain Alembic and security-definer access.
DO $policy$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policy
        WHERE polrelid = 'space.environment_control'::regclass
          AND polname = 'environment_status_read'
    ) THEN
        CREATE POLICY environment_status_read ON space.environment_control
            FOR SELECT TO space_app USING (true);
    END IF;
END;
$policy$;
