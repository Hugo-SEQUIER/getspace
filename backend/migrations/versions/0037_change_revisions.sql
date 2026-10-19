-- Which kinds of content changed at which Space content revision. A write that bumps
-- content_revision also records the new revision under each kind it changed, such as
-- {"document": 41, "memory": 41, "project": 12}. The website compares these per kind,
-- so a memory saved by an agent no longer reloads every project, folder and database
-- list. Keys and numbers only, never content. A missing key means no recorded change.
ALTER TABLE space.spaces
    ADD COLUMN IF NOT EXISTS change_revisions jsonb NOT NULL DEFAULT '{}'::jsonb;
GRANT UPDATE (change_revisions) ON space.spaces TO space_app;
