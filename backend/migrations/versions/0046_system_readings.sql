-- Parameters: the workspace item kind check from `0043_systems`.
-- What it does: lets a workspace item be a system's readings for one calendar month: the
-- dated numbers a sensor script records, at most one per date.
-- Output: Systems commands store each month as a packed workspace item, so a replaced
-- reading stays in that month's revisions.
ALTER TABLE space.workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE space.workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('project','database','category','section','folder','system','lab_entry',
                    'system_readings'));
