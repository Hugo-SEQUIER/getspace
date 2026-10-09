ALTER TABLE space.workspace_items DROP CONSTRAINT workspace_items_kind_check;
ALTER TABLE space.workspace_items ADD CONSTRAINT workspace_items_kind_check
    CHECK (kind IN ('project','database','category','section'));
