CREATE TABLE space.memory_jobs (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    issuer text NOT NULL,
    subject text NOT NULL,
    client_id uuid,
    grant_id uuid,
    request_kind text NOT NULL CHECK (request_kind IN ('remember','build')),
    key_id uuid NOT NULL,
    encrypted_request bytea NOT NULL,
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint)=32),
    provider_fingerprint text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_id uuid,
    lease_until timestamptz,
    result_resource_id uuid,
    result_revision integer,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,key_id)
        REFERENCES space.space_keys(organization_id,space_id,id),
    CHECK ((client_id IS NULL)=(grant_id IS NULL))
);
CREATE INDEX memory_jobs_ready_idx ON space.memory_jobs (available_at,created_at)
    WHERE status IN ('queued','processing');

-- A dedicated login may SET ROLE to this NOLOGIN capability role. The runtime
-- must never be made a member; it cannot update attempts, leases or outcomes.
DO $worker$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='space_job_worker') THEN
        CREATE ROLE space_job_worker NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        COMMENT ON ROLE space_job_worker IS 'space.memory_jobs.worker.v1';
    ELSIF (SELECT shobj_description(oid,'pg_authid') FROM pg_roles
           WHERE rolname='space_job_worker') IS DISTINCT FROM 'space.memory_jobs.worker.v1' THEN
        RAISE EXCEPTION 'Unrelated space_job_worker role exists';
    END IF;
END
$worker$;
GRANT USAGE ON SCHEMA space TO space_job_worker;
GRANT SELECT ON space.memory_jobs TO space_job_worker;
GRANT UPDATE (status,attempts,available_at,lease_id,lease_until,result_resource_id,
              result_revision,error_code,updated_at) ON space.memory_jobs TO space_job_worker;
GRANT SELECT, INSERT ON space.memory_jobs TO space_app;
ALTER TABLE space.memory_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.memory_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.memory_jobs TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id=memory_jobs.organization_id AND s.id=memory_jobs.space_id)
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id=memory_jobs.organization_id AND s.id=memory_jobs.space_id)
);
CREATE POLICY job_worker ON space.memory_jobs TO space_job_worker USING (true) WITH CHECK (true);
DO $maintenance$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.memory_jobs TO %I '
                   'USING (true) WITH CHECK (true)', current_user);
END
$maintenance$;
REVOKE ALL ON space.memory_jobs FROM PUBLIC;

-- Lease checks hold a shared row lock until the application transaction commits.
-- The runtime receives no UPDATE privilege on the job's immutable input or status.
CREATE FUNCTION space.memory_job_lease_active(job_id uuid, receipt uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,space AS $lease$
BEGIN
    PERFORM j.id FROM space.memory_jobs j JOIN space.principals p ON p.id=j.principal_id
        WHERE j.organization_id=space.requested_organization_id()
        AND j.space_id=space.requested_space_id() AND j.id=job_id AND j.lease_id=receipt
        AND j.status='processing' AND j.lease_until>now()
        AND p.issuer=current_setting('space.identity_issuer',true)
        AND p.subject=current_setting('space.identity_subject',true) FOR SHARE OF j;
    RETURN FOUND;
END
$lease$;
REVOKE ALL ON FUNCTION space.memory_job_lease_active(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.memory_job_lease_active(uuid,uuid) TO space_app;
