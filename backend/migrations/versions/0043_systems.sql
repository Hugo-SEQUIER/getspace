-- Systems: a system is a recurring activity with a goal, a health status and a playbook; a lab
-- entry is a hypothesis or lesson it tests. Both are encrypted workspace items with the usual
-- revisions. Only a promoted lab entry (a verdict) reaches recall, as a memory it writes.
ALTER TABLE space.workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE space.workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('project','database','category','section','folder','system','lab_entry'));
