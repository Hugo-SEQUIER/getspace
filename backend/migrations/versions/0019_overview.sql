-- Existing content stays encrypted; unknown historical line counts stay NULL.
ALTER TABLE space.spaces ADD COLUMN activity_started_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE space.resources ADD COLUMN content_lines integer CHECK (content_lines >= 0);
GRANT UPDATE (content_lines) ON space.resources TO space_app;

CREATE TABLE space.activity_events (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    agent_id uuid,
    actor_type text NOT NULL CHECK (actor_type IN ('agent','website')),
    kind text NOT NULL CHECK (kind IN ('document','memory','task','handoff','retrieval')),
    resource_id uuid,
    new_items integer NOT NULL DEFAULT 0 CHECK (new_items >= 0),
    item_count integer NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    outcome text NOT NULL DEFAULT 'success' CHECK (outcome IN ('success','empty','error')),
    duration_ms double precision CHECK (duration_ms >= 0 AND duration_ms < 'Infinity'::float8),
    strategy text CHECK (strategy IN ('auto','lexical','semantic','prompt','resume')),
    returned_claims uuid[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id) REFERENCES space.spaces(organization_id,id),
    FOREIGN KEY (organization_id,space_id,agent_id) REFERENCES space.agent_identities(organization_id,space_id,id),
    CHECK (cardinality(returned_claims) <= 100)
);
CREATE INDEX activity_events_period ON space.activity_events(organization_id,space_id,created_at DESC);
CREATE INDEX activity_events_retention ON space.activity_events(created_at);
ALTER TABLE space.activity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.activity_events FORCE ROW LEVEL SECURITY;
CREATE POLICY scoped_activity ON space.activity_events TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=activity_events.organization_id AND s.id=activity_events.space_id)
) WITH CHECK (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=activity_events.organization_id AND s.id=activity_events.space_id)
);
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.activity_events TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
-- Viewers can produce retrieval telemetry, but cannot change content or history.
GRANT SELECT,INSERT ON space.activity_events TO space_app;
REVOKE ALL ON space.activity_events FROM PUBLIC;
