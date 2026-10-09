-- A readable memory marker for a resource, so recall can select memory in SQL.
--
-- Why: a memory is stored with kind = 'document', because its payload has the same shape
-- as a source document. Nothing readable distinguished it, so finding the Space's memories
-- meant decrypting the metadata of every resource in the Space. That is what capped a
-- Space at 512 resources for recall, and it also forced the brain browser and every build
-- or remember call to read the whole corpus.
--
-- The value mirrors the encrypted recall category exactly: NULL for a source document or
-- task, 'handoff' for a handoff, 'memory' for a generated memory. A handoff stays
-- lexically searchable as source evidence, while only a generated memory takes part in the
-- claim index, so one column answers every filter recall needs without a decryption.
--
-- The client-visible resource kind stays 'document' or 'task' (see _summary in
-- workspace.py), so this column changes no API shape. The write path sets it whenever a
-- resource is written; existing memory resources are labelled by
-- scripts/index_resource_terms during the backfill, which already decrypts each resource.
SET LOCAL search_path = space, public;

ALTER TABLE space.resources
    ADD COLUMN memory_kind text CHECK (memory_kind IS NULL OR memory_kind IN ('handoff', 'memory'));

-- Recall reads memories by recency; the brain browser lists them the same way.
CREATE INDEX resources_memory_idx ON space.resources (organization_id, space_id, created_at)
    WHERE memory_kind IS NOT NULL;

-- The runtime login has column-level UPDATE on this table (migration 0002), so a written
-- column needs its own grant. It remains unable to rewrite identity columns.
GRANT UPDATE (memory_kind) ON space.resources TO space_app;
