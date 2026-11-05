-- Parameters: the workspace item tables from `0012_workspace_items` and the storage trigger and
-- head `0041_document_files` left.
-- What it does: adds one packed row per database record, with its own history, and counts those
-- rows toward retained storage.
-- Output: a database edit stores the rows it changed instead of a copy of the whole table.
--
-- A database item revision keeps the schema and the metadata; each record is its own packed row
-- with its own history, so an edit costs the size of the row it changes rather than the whole
-- table, and a table is no longer bounded by one revision payload. Records saved before this
-- migration stay inside their item revision payloads, where every read of those revisions still
-- finds them; the database's next write moves its current records here.
CREATE TABLE space.database_records (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    database_id uuid NOT NULL,
    id text NOT NULL CHECK (length(id) BETWEEN 1 AND 100),
    current_revision integer NOT NULL CHECK (current_revision > 0),
    deleted boolean NOT NULL DEFAULT false,
    -- Insertion order within the database; readers return rows in this order.
    position bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, database_id, id),
    FOREIGN KEY (organization_id, space_id, database_id)
        REFERENCES space.workspace_items(organization_id, space_id, id)
);
CREATE INDEX database_records_position
    ON space.database_records (organization_id, space_id, database_id, position);
-- One row per version of a record, keyed by the database revision that wrote it. A deletion is a
-- tombstone revision without a payload, so history reads at an earlier revision still find the
-- record.
CREATE TABLE space.database_record_revisions (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    database_id uuid NOT NULL,
    record_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    key_id uuid NOT NULL,
    encrypted_payload bytea CHECK (octet_length(encrypted_payload) BETWEEN 1 AND 1500000),
    deleted boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, database_id, record_id, revision),
    CHECK (deleted = (encrypted_payload IS NULL)),
    FOREIGN KEY (organization_id, space_id, database_id, record_id)
        REFERENCES space.database_records(organization_id, space_id, database_id, id),
    FOREIGN KEY (organization_id, space_id, database_id, revision)
        REFERENCES space.workspace_item_revisions(organization_id, space_id, item_id, revision),
    FOREIGN KEY (organization_id, space_id, key_id)
        REFERENCES space.space_keys(organization_id, space_id, id)
);
ALTER TABLE space.database_records ADD CONSTRAINT database_record_current_revision_fk
    FOREIGN KEY (organization_id, space_id, database_id, id, current_revision)
    REFERENCES space.database_record_revisions(organization_id, space_id, database_id, record_id, revision)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE space.database_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.database_records FORCE ROW LEVEL SECURITY;
ALTER TABLE space.database_record_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.database_record_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.database_records TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = database_records.organization_id
                  AND s.id = database_records.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = database_records.organization_id
                  AND s.id = database_records.space_id)
);
CREATE POLICY private_owner ON space.database_record_revisions TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = database_record_revisions.organization_id
                  AND s.id = database_record_revisions.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = database_record_revisions.organization_id
                  AND s.id = database_record_revisions.space_id)
);
-- As for every other content table, viewers of a team Space read but never write.
CREATE POLICY member_insert ON space.database_records AS RESTRICTIVE FOR INSERT TO space_app
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_update ON space.database_records AS RESTRICTIVE FOR UPDATE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'))
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_delete ON space.database_records AS RESTRICTIVE FOR DELETE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_insert ON space.database_record_revisions AS RESTRICTIVE FOR INSERT TO space_app
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_update ON space.database_record_revisions AS RESTRICTIVE FOR UPDATE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'))
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_delete ON space.database_record_revisions AS RESTRICTIVE FOR DELETE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.database_records TO %I USING (true) WITH CHECK (true)',current_user);
    EXECUTE format('CREATE POLICY maintenance_owner ON space.database_record_revisions TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
REVOKE ALL ON space.database_records FROM PUBLIC;
REVOKE ALL ON space.database_record_revisions FROM PUBLIC;
GRANT SELECT, INSERT ON space.database_records TO space_app;
GRANT SELECT, INSERT ON space.database_record_revisions TO space_app;
-- A write moves a record's head to the revision it just stored, or marks it deleted.
GRANT UPDATE (current_revision,deleted) ON space.database_records TO space_app;

-- Record revisions count towards retained storage like every other stored payload. The function
-- is the one `0041_document_files` installed, with database_record_revisions counted beside
-- workspace_item_revisions; a tombstone has no payload and costs nothing.
CREATE OR REPLACE FUNCTION space.account_content_usage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $function$
DECLARE org uuid; sid uuid; old_bytes bigint:=0; new_bytes bigint:=0;
    delta_items integer:=0; limits record;
BEGIN
    IF TG_OP='DELETE' THEN org:=OLD.organization_id; sid:=OLD.space_id;
    ELSE org:=NEW.organization_id; sid:=NEW.space_id; END IF;
    IF TG_TABLE_NAME IN ('resources','workspace_items') THEN
        IF TG_OP='INSERT' THEN delta_items:=1;
        ELSIF TG_OP='DELETE' THEN delta_items:=-1; END IF;
    ELSIF TG_TABLE_NAME='resource_revisions' THEN
        IF TG_OP!='DELETE' THEN new_bytes:=NEW.content_bytes+octet_length(NEW.encrypted_metadata); END IF;
        IF TG_OP!='INSERT' THEN old_bytes:=OLD.content_bytes+octet_length(OLD.encrypted_metadata); END IF;
    ELSIF TG_TABLE_NAME IN ('restore_objects','file_parts') THEN
        IF TG_OP!='DELETE' THEN new_bytes:=NEW.content_bytes; END IF;
        IF TG_OP!='INSERT' THEN old_bytes:=OLD.content_bytes; END IF;
    ELSIF TG_TABLE_NAME IN ('workspace_item_revisions','database_record_revisions') THEN
        IF TG_OP!='DELETE' THEN new_bytes:=coalesce(octet_length(NEW.encrypted_payload),0); END IF;
        IF TG_OP!='INSERT' THEN old_bytes:=coalesce(octet_length(OLD.encrypted_payload),0); END IF;
    ELSE
        IF TG_OP!='DELETE' THEN new_bytes:=octet_length(NEW.encrypted_metadata); END IF;
        IF TG_OP!='INSERT' THEN old_bytes:=octet_length(OLD.encrypted_metadata); END IF;
    END IF;
    INSERT INTO space.usage_limits(organization_id,space_id) VALUES(org,sid) ON CONFLICT DO NOTHING;
    SELECT * INTO limits FROM space.usage_limits WHERE organization_id=org AND space_id=sid FOR UPDATE;
    IF new_bytes>old_bytes AND limits.retained_bytes+new_bytes-old_bytes>limits.storage_limit_bytes
        AND coalesce(current_setting('space.quota_deletion',true),'')!='on' THEN
        RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='space.storage_quota';
    END IF;
    IF delta_items>0 AND limits.items+delta_items>limits.item_limit THEN
        RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='space.item_quota';
    END IF;
    UPDATE space.usage_limits SET retained_bytes=retained_bytes+new_bytes-old_bytes,items=items+delta_items
        WHERE organization_id=org AND space_id=sid;
    RETURN NULL;
END
$function$;
REVOKE ALL ON FUNCTION space.account_content_usage() FROM PUBLIC;
CREATE TRIGGER content_usage AFTER INSERT OR UPDATE OR DELETE ON space.database_record_revisions
    FOR EACH ROW EXECUTE FUNCTION space.account_content_usage();
