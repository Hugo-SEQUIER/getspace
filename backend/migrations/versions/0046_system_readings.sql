-- Parameters: the workspace items from `0012_workspace_items`, the systems kind from
-- `0043_systems` and the head `0045_pending_caps` left.
-- What it does: adds one row per system, date and metric for the dated numbers a sensor script
-- records. A second reading for the same date and metric updates its row in place.
-- Output: readings keep no revision history, so an owner archive carries one entry per stored
-- reading and never a revision per write (restore refuses more than 10,000 revisions).
CREATE TABLE space.system_readings (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    system_id uuid NOT NULL,
    day date NOT NULL,
    metric text NOT NULL CHECK (length(metric) <= 64 AND metric ~ '^[a-z0-9][a-z0-9_.-]*$'),
    value double precision NOT NULL
        CHECK (value <> 'NaN'::float8 AND value <> 'Infinity'::float8
               AND value <> '-Infinity'::float8),
    unit text NOT NULL DEFAULT '' CHECK (length(unit) <= 40),
    note text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    source text NOT NULL CHECK (length(source) BETWEEN 1 AND 200),
    -- The agent that recorded it; null when the person did.
    recorded_by text CHECK (length(recorded_by) <= 200),
    -- The command that last wrote the row; a retry of that command is answered from here.
    command_id uuid NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, system_id, day, metric),
    FOREIGN KEY (organization_id, space_id, system_id)
        REFERENCES space.workspace_items(organization_id, space_id, id)
);
CREATE INDEX system_readings_command
    ON space.system_readings (organization_id, space_id, command_id);
ALTER TABLE space.system_readings ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.system_readings FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.system_readings TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = system_readings.organization_id
                  AND s.id = system_readings.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = system_readings.organization_id
                  AND s.id = system_readings.space_id)
);
-- As for every other content table, viewers of a team Space read but never write.
CREATE POLICY member_insert ON space.system_readings AS RESTRICTIVE FOR INSERT TO space_app
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_update ON space.system_readings AS RESTRICTIVE FOR UPDATE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'))
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.system_readings TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
REVOKE ALL ON space.system_readings FROM PUBLIC;
GRANT SELECT, INSERT ON space.system_readings TO space_app;
-- A replaced reading changes its number and provenance; its key never moves.
GRANT UPDATE (value,unit,note,source,recorded_by,command_id,recorded_at)
    ON space.system_readings TO space_app;
