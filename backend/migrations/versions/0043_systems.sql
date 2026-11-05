-- Parameters: the workspace item kind check from `0012_workspace_items`.
-- What it does: lets a workspace item be a system, a recurring activity with a goal, a health
-- status and a playbook, or a lab entry, a hypothesis or lesson that system tests.
-- Output: Systems commands store both kinds as packed workspace items with the usual
-- revisions. Only a promoted lab entry (a verdict) reaches recall, as a memory it writes.
ALTER TABLE space.workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE space.workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('project','database','category','section','folder','system','lab_entry'));
