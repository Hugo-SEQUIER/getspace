ALTER TABLE space.oauth_grants ADD COLUMN last_mcp_activity_at timestamptz;
GRANT UPDATE (last_mcp_activity_at) ON space.oauth_grants TO space_app;
CREATE INDEX oauth_grants_activity_idx
    ON space.oauth_grants (resource, last_mcp_activity_at)
    WHERE last_mcp_activity_at IS NOT NULL AND revoked_at IS NULL;

-- The existing (principal_id, client_id) primary key survives token refresh and
-- grant rotation on reconnect. No events, tool arguments or content are stored.
COMMENT ON COLUMN space.oauth_grants.last_mcp_activity_at IS
    'Last successful authorized workspace MCP call recorded for public usage reporting';
