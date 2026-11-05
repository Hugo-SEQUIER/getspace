-- Parameters: the Spaces, resources and storage accounting from earlier migrations.
-- What it does: adds the ledger of file parts a document's original file is stored in, and
-- counts those parts towards storage. This edition keeps the part bytes in the row itself,
-- as it keeps document bodies, so there is no object key to clean up.
-- Output: a document can keep its original PDF, Word, PowerPoint or other file.
CREATE TABLE space.file_parts (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    file_id uuid NOT NULL,
    part integer NOT NULL CHECK (part BETWEEN 0 AND 249),
    resource_id uuid,
    content bytea NOT NULL CHECK (octet_length(content) BETWEEN 1 AND 1048576),
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint)=32),
    plain_bytes integer NOT NULL CHECK (plain_bytes BETWEEN 1 AND 1048576),
    content_bytes integer NOT NULL CHECK (content_bytes BETWEEN 1 AND 1500000),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id, space_id, file_id, part),
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id),
    -- No cascade: deleting a document must free its parts in the same transaction.
    FOREIGN KEY (organization_id, space_id, resource_id)
        REFERENCES space.resources(organization_id, space_id, id)
);
CREATE INDEX file_parts_resource_idx ON space.file_parts (organization_id, space_id, resource_id)
    WHERE resource_id IS NOT NULL;
CREATE INDEX file_parts_staged_idx ON space.file_parts (created_at) WHERE resource_id IS NULL;
ALTER TABLE space.file_parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.file_parts FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.file_parts TO space_app USING (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = file_parts.organization_id
                  AND s.id = file_parts.space_id)
) WITH CHECK (
    organization_id = space.requested_organization_id()
    AND space_id = space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s
                WHERE s.organization_id = file_parts.organization_id
                  AND s.id = file_parts.space_id)
);
-- As for every other content table, viewers of a team Space read but never write.
CREATE POLICY member_insert ON space.file_parts AS RESTRICTIVE FOR INSERT TO space_app
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_update ON space.file_parts AS RESTRICTIVE FOR UPDATE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'))
    WITH CHECK (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
CREATE POLICY member_delete ON space.file_parts AS RESTRICTIVE FOR DELETE TO space_app
    USING (space.space_role(organization_id,space_id) IN ('owner','admin','member'));
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.file_parts TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
REVOKE ALL ON space.file_parts FROM PUBLIC;
GRANT SELECT, INSERT, DELETE ON space.file_parts TO space_app;
-- Committing a file attaches its staged parts; nothing else about a part ever changes.
GRANT UPDATE (resource_id) ON space.file_parts TO space_app;

-- Stored parts count towards storage like every other stored payload. The function is the
-- one 0014 installed, with file_parts counted beside restore_objects.
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
    ELSIF TG_TABLE_NAME='workspace_item_revisions' THEN
        IF TG_OP!='DELETE' THEN new_bytes:=octet_length(NEW.encrypted_payload); END IF;
        IF TG_OP!='INSERT' THEN old_bytes:=octet_length(OLD.encrypted_payload); END IF;
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
CREATE TRIGGER content_usage AFTER INSERT OR UPDATE OR DELETE ON space.file_parts
    FOR EACH ROW EXECUTE FUNCTION space.account_content_usage();
