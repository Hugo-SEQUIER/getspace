-- Parameters: the webhook subscriptions and transactional outbox from 0027.
-- What it does: adds an agent inbox delivery kind that agents drain over MCP instead of an HTTPS receiver.
-- Output: a plain delivery column the worker filters on, outbox filters that report it, and owner-scoped inbox receipts.
ALTER TABLE space.webhook_subscriptions
    ADD COLUMN delivery text NOT NULL DEFAULT 'endpoint' CHECK (delivery IN ('endpoint','inbox'));

DROP FUNCTION space.active_webhook_subscriptions(uuid);
CREATE FUNCTION space.active_webhook_subscriptions(selected_database uuid)
RETURNS TABLE(id uuid,revision integer,key_id uuid,encrypted_filter bytea,delivery text)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    SELECT s.id,s.revision,s.key_id,s.encrypted_filter,s.delivery FROM space.webhook_subscriptions s
    WHERE s.organization_id=space.requested_organization_id() AND s.space_id=space.requested_space_id()
    AND s.database_id=selected_database AND NOT s.paused AND s.removed_at IS NULL
    AND space.space_role(s.organization_id,s.space_id) IN ('owner','admin','member')
$$;
REVOKE ALL ON FUNCTION space.active_webhook_subscriptions(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.active_webhook_subscriptions(uuid) TO space_app;

-- Parameters: an inbox delivery the owner's agent leased or acknowledged, with its cycle and attempt.
-- What it does: records the attempt receipt without granting the runtime role table privileges on receipts.
-- Output: one started receipt per pull, finished on acknowledgement; endpoint deliveries are untouched.
CREATE FUNCTION space.inbox_attempt_started(selected_delivery uuid,selected_cycle integer,selected_attempt integer)
RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    WITH inserted AS (
        INSERT INTO space.webhook_attempts (organization_id,space_id,delivery_id,cycle,attempt,started_at)
        SELECT d.organization_id,d.space_id,d.id,selected_cycle,selected_attempt,clock_timestamp()
        FROM space.webhook_deliveries d JOIN space.webhook_subscriptions s
            ON (s.organization_id,s.space_id,s.id)=(d.organization_id,d.space_id,d.subscription_id)
        WHERE d.organization_id=space.requested_organization_id() AND d.space_id=space.requested_space_id()
        AND d.id=selected_delivery AND s.delivery='inbox'
        AND space.space_role(d.organization_id,d.space_id)='owner'
        RETURNING 1
    ) SELECT count(*)::integer FROM inserted
$$;
CREATE FUNCTION space.inbox_attempt_finished(selected_delivery uuid,selected_cycle integer,selected_attempt integer,code text)
RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    WITH updated AS (
        UPDATE space.webhook_attempts a SET finished_at=clock_timestamp(),status_code=NULL,error_code=code
        FROM space.webhook_deliveries d JOIN space.webhook_subscriptions s
            ON (s.organization_id,s.space_id,s.id)=(d.organization_id,d.space_id,d.subscription_id)
        WHERE (a.organization_id,a.space_id,a.delivery_id)=(d.organization_id,d.space_id,d.id)
        AND d.organization_id=space.requested_organization_id() AND d.space_id=space.requested_space_id()
        AND d.id=selected_delivery AND a.cycle=selected_cycle AND a.attempt=selected_attempt
        AND a.finished_at IS NULL AND s.delivery='inbox'
        AND space.space_role(d.organization_id,d.space_id)='owner'
        RETURNING 1
    ) SELECT count(*)::integer FROM updated
$$;
REVOKE ALL ON FUNCTION space.inbox_attempt_started(uuid,integer,integer),
    space.inbox_attempt_finished(uuid,integer,integer,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.inbox_attempt_started(uuid,integer,integer),
    space.inbox_attempt_finished(uuid,integer,integer,text) TO space_app;
