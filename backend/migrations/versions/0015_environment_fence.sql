CREATE TABLE space.environment_control (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    environment_id uuid NOT NULL,
    name text NOT NULL CHECK (name IN ('development','test','staging','production')),
    serving boolean NOT NULL DEFAULT false,
    changed_at timestamptz NOT NULL DEFAULT now(),
    recovery_verified_at timestamptz,
    recovered_backup_id uuid,
    recovered_source_environment_id uuid
);
REVOKE ALL ON space.environment_control FROM PUBLIC;
GRANT SELECT ON space.environment_control TO space_app;
-- SELECT FOR SHARE needs an UPDATE privilege. The runtime uses a narrowly scoped
-- security-definer function instead of receiving any mutation privilege.
CREATE FUNCTION space.check_environment(expected uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
DECLARE item space.environment_control;
BEGIN
    SELECT * INTO item FROM space.environment_control WHERE singleton FOR SHARE;
    IF NOT FOUND THEN RETURN expected IS NULL; END IF;
    RETURN expected IS NOT NULL AND item.environment_id=expected AND item.serving;
END $$;
REVOKE ALL ON FUNCTION space.check_environment(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.check_environment(uuid) TO space_app;
GRANT EXECUTE ON FUNCTION space.check_environment(uuid) TO space_job_worker;

CREATE TABLE space.erased_subjects (
    subject_hash bytea PRIMARY KEY CHECK(octet_length(subject_hash)=32),
    erased_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON space.erased_subjects FROM PUBLIC;
CREATE FUNCTION space.identity_erased(candidate bytea) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM space.erased_subjects WHERE subject_hash=candidate)
$$;
REVOKE ALL ON FUNCTION space.identity_erased(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.identity_erased(bytea) TO space_app;
CREATE TABLE space.deletion_receipts (
    id uuid PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON space.deletion_receipts FROM PUBLIC;
