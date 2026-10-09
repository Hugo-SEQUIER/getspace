CREATE TABLE space.memory_job_schedule (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    last_claimed_at timestamptz,
    PRIMARY KEY (organization_id,space_id),
    FOREIGN KEY (organization_id,space_id)
        REFERENCES space.spaces(organization_id,id) ON DELETE CASCADE
);

GRANT SELECT ON space.memory_job_schedule TO space_app, space_job_worker;
GRANT INSERT (organization_id,space_id) ON space.memory_job_schedule TO space_app;
GRANT UPDATE (last_claimed_at) ON space.memory_job_schedule TO space_job_worker;
ALTER TABLE space.memory_job_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.memory_job_schedule FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.memory_job_schedule TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id=memory_job_schedule.organization_id
                AND s.id=memory_job_schedule.space_id)
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id=memory_job_schedule.organization_id
                AND s.id=memory_job_schedule.space_id)
);
CREATE POLICY job_worker ON space.memory_job_schedule TO space_job_worker
    USING (true) WITH CHECK (true);
DO $maintenance$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.memory_job_schedule TO %I '
                   'USING (true) WITH CHECK (true)', current_user);
END
$maintenance$;
REVOKE ALL ON space.memory_job_schedule FROM PUBLIC;

-- The trigger also covers enqueues by an older API during a rolling deployment.
-- It runs with the caller's privileges and existing Space scope, without elevation.
CREATE FUNCTION space.schedule_memory_job() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,space AS $schedule$
BEGIN
    INSERT INTO space.memory_job_schedule (organization_id,space_id)
        VALUES (NEW.organization_id,NEW.space_id) ON CONFLICT DO NOTHING;
    RETURN NEW;
END
$schedule$;
REVOKE ALL ON FUNCTION space.schedule_memory_job() FROM PUBLIC;
CREATE TRIGGER memory_jobs_schedule AFTER INSERT ON space.memory_jobs
    FOR EACH ROW EXECUTE FUNCTION space.schedule_memory_job();

-- Creating the trigger first locks out concurrent inserts until this migration
-- commits, so none can slip between the backfill and trigger installation.
INSERT INTO space.memory_job_schedule (organization_id,space_id)
    SELECT DISTINCT organization_id,space_id FROM space.memory_jobs ON CONFLICT DO NOTHING;

CREATE INDEX memory_jobs_space_ready_idx
    ON space.memory_jobs (organization_id,space_id,available_at,created_at,id)
    WHERE status IN ('queued','processing');
