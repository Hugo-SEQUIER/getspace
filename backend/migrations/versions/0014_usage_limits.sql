-- Older object lengths are unavailable in SQL. Charge their maximum supported envelope
-- conservatively until the operator reconciles them against object metadata.
ALTER TABLE space.resource_revisions ADD COLUMN content_bytes integer NOT NULL DEFAULT 1400152
    CHECK (content_bytes BETWEEN 0 AND 1500000);
UPDATE space.resource_revisions SET content_bytes=octet_length(encrypted_record) WHERE encrypted_record IS NOT NULL;
ALTER TABLE space.restore_objects ADD COLUMN content_bytes integer NOT NULL DEFAULT 1400152
    CHECK (content_bytes BETWEEN 1 AND 1500000);
CREATE TABLE space.usage_limits (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    retained_bytes bigint NOT NULL DEFAULT 0 CHECK (retained_bytes>=0),
    items bigint NOT NULL DEFAULT 0 CHECK (items>=0),
    storage_limit_bytes bigint NOT NULL DEFAULT 536870912 CHECK (storage_limit_bytes>0),
    item_limit integer NOT NULL DEFAULT 10000 CHECK (item_limit>0),
    month date NOT NULL DEFAULT date_trunc('month',now() AT TIME ZONE 'UTC')::date,
    model_requests integer NOT NULL DEFAULT 0 CHECK (model_requests>=0),
    model_request_limit integer NOT NULL DEFAULT 2000 CHECK (model_request_limit>=0),
    PRIMARY KEY (organization_id,space_id),
    FOREIGN KEY (organization_id,space_id) REFERENCES space.spaces(organization_id,id)
);
INSERT INTO space.usage_limits(organization_id,space_id,retained_bytes,items)
SELECT s.organization_id,s.id,
    coalesce((SELECT sum(v.content_bytes+octet_length(v.encrypted_metadata)) FROM space.resource_revisions v WHERE v.organization_id=s.organization_id AND v.space_id=s.id),0)
    +coalesce((SELECT sum(octet_length(v.encrypted_payload)) FROM space.workspace_item_revisions v WHERE v.organization_id=s.organization_id AND v.space_id=s.id),0)
    +coalesce((SELECT sum(u.content_bytes) FROM space.restore_objects u WHERE u.organization_id=s.organization_id AND u.space_id=s.id),0)
    +coalesce((SELECT sum(octet_length(a.encrypted_metadata)) FROM space.agent_identities a WHERE a.organization_id=s.organization_id AND a.space_id=s.id),0),
    (SELECT count(*) FROM space.resources r WHERE r.organization_id=s.organization_id AND r.space_id=s.id)
    +(SELECT count(*) FROM space.workspace_items i WHERE i.organization_id=s.organization_id AND i.space_id=s.id)
FROM space.spaces s;
ALTER TABLE space.usage_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.usage_limits FORCE ROW LEVEL SECURITY;
CREATE POLICY private_owner ON space.usage_limits TO space_app USING (
    organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
    AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id=usage_limits.organization_id AND s.id=usage_limits.space_id)
);
DO $policy$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.usage_limits TO %I USING (true) WITH CHECK (true)',current_user);
END
$policy$;
GRANT SELECT ON space.usage_limits TO space_app;
REVOKE ALL ON space.usage_limits FROM PUBLIC;

CREATE FUNCTION space.account_content_usage() RETURNS trigger
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
    ELSIF TG_TABLE_NAME='restore_objects' THEN
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
DO $triggers$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['resources','workspace_items','resource_revisions','workspace_item_revisions','agent_identities','restore_objects'] LOOP
        EXECUTE format('CREATE TRIGGER content_usage AFTER INSERT OR UPDATE OR DELETE ON space.%I FOR EACH ROW EXECUTE FUNCTION space.account_content_usage()',t);
    END LOOP;
END
$triggers$;

CREATE FUNCTION space.consume_model_request() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $function$
DECLARE org uuid:=space.requested_organization_id(); sid uuid:=space.requested_space_id();
    current_month date:=date_trunc('month',now() AT TIME ZONE 'UTC')::date; used integer;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM space.spaces s JOIN space.organizations o ON o.id=s.organization_id
        JOIN space.principals p ON p.id=o.owner_principal_id
        WHERE s.organization_id=org AND s.id=sid AND p.issuer=current_setting('space.identity_issuer',true)
        AND p.subject=current_setting('space.identity_subject',true)) THEN
        RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='space.invalid_scope';
    END IF;
    INSERT INTO space.usage_limits(organization_id,space_id) VALUES(org,sid) ON CONFLICT DO NOTHING;
    UPDATE space.usage_limits SET month=current_month,
        model_requests=CASE WHEN month=current_month THEN model_requests+1 ELSE 1 END
        WHERE organization_id=org AND space_id=sid
        AND (CASE WHEN month=current_month THEN model_requests ELSE 0 END)<model_request_limit
        RETURNING model_requests INTO used;
    IF used IS NULL THEN RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='space.model_quota'; END IF;
    RETURN used;
END
$function$;
REVOKE ALL ON FUNCTION space.consume_model_request() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.consume_model_request() TO space_app;

-- Deliberately aggregate-only: no content, account IDs, job IDs or tokens.
CREATE FUNCTION space.operational_health() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $function$
    SELECT jsonb_build_object(
        'queued_jobs',(SELECT count(*) FROM space.memory_jobs WHERE status='queued'),
        'processing_jobs',(SELECT count(*) FROM space.memory_jobs WHERE status='processing'),
        'failed_jobs_24h',(SELECT count(*) FROM space.memory_jobs WHERE status='failed' AND updated_at>now()-interval '24 hours'),
        'oldest_queued_seconds',(SELECT coalesce(max(extract(epoch FROM now()-created_at)),0) FROM space.memory_jobs WHERE status='queued'),
        'expired_job_leases',(SELECT count(*) FROM space.memory_jobs WHERE status='processing' AND lease_until<now()),
        'pending_object_cleanup',(SELECT count(*) FROM space.purge_cleanup)+(SELECT count(*) FROM space.restore_objects WHERE created_at<now()-interval '1 hour'),
        'expired_restore_uploads',(SELECT count(*) FROM space.restore_sessions WHERE expires_at<now()),
        'spaces_at_storage_limit',(SELECT count(*) FROM space.usage_limits WHERE retained_bytes>=storage_limit_bytes),
        'spaces_at_model_limit',(SELECT count(*) FROM space.usage_limits WHERE month=date_trunc('month',now() AT TIME ZONE 'UTC')::date AND model_requests>=model_request_limit)
    );
$function$;
REVOKE ALL ON FUNCTION space.operational_health() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.operational_health() TO space_app;
