-- Parameters: existing Space workspace and scoped authorization schema.
-- What it does: adds encrypted webhook subscriptions, a transactional outbox and receipts.
-- Output: isolated owner management and a non-privileged delivery capability; no worker activation.
CREATE TABLE space.webhook_subscriptions (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    database_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    paused boolean NOT NULL DEFAULT false,
    removed_at timestamptz,
    key_id uuid NOT NULL,
    encrypted_configuration bytea NOT NULL,
    encrypted_filter bytea NOT NULL,
    created_by uuid NOT NULL REFERENCES space.principals(id),
    issuer text NOT NULL,
    subject text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,database_id)
        REFERENCES space.workspace_items(organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,key_id)
        REFERENCES space.space_keys(organization_id,space_id,id)
);
CREATE TABLE space.webhook_events (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    database_id uuid NOT NULL,
    record_id text NOT NULL CHECK (length(record_id) BETWEEN 1 AND 100),
    previous_revision integer NOT NULL CHECK (previous_revision >= 0),
    revision integer NOT NULL CHECK (revision > previous_revision),
    operation text NOT NULL CHECK (operation IN ('created','updated','deleted')),
    key_id uuid NOT NULL,
    encrypted_payload bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    UNIQUE (organization_id,space_id,database_id,record_id,revision),
    FOREIGN KEY (organization_id,space_id,database_id)
        REFERENCES space.workspace_items(organization_id,space_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (organization_id,space_id,key_id)
        REFERENCES space.space_keys(organization_id,space_id,id)
);
CREATE TABLE space.webhook_deliveries (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    event_id uuid NOT NULL,
    key_id uuid NOT NULL,
    encrypted_payload bytea NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','delivered','failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    cycle integer NOT NULL DEFAULT 0 CHECK (cycle BETWEEN 0 AND 20),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_id uuid,
    lease_until timestamptz,
    status_code integer,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    UNIQUE (organization_id,space_id,subscription_id,event_id),
    FOREIGN KEY (organization_id,space_id,subscription_id)
        REFERENCES space.webhook_subscriptions(organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,event_id)
        REFERENCES space.webhook_events(organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,key_id)
        REFERENCES space.space_keys(organization_id,space_id,id)
);
CREATE INDEX webhook_deliveries_ready_idx ON space.webhook_deliveries (available_at,created_at)
    WHERE status IN ('queued','processing');
CREATE TABLE space.webhook_attempts (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    delivery_id uuid NOT NULL,
    cycle integer NOT NULL CHECK (cycle BETWEEN 0 AND 20),
    attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status_code integer,
    error_code text,
    PRIMARY KEY (organization_id,space_id,delivery_id,cycle,attempt),
    FOREIGN KEY (organization_id,space_id,delivery_id)
        REFERENCES space.webhook_deliveries(organization_id,space_id,id) ON DELETE CASCADE
);
CREATE TABLE space.webhook_commands (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    id uuid NOT NULL,
    subscription_id uuid NOT NULL,
    fingerprint bytea NOT NULL CHECK (octet_length(fingerprint)=32),
    action text NOT NULL CHECK (action IN ('create','pause','resume','remove','rotate','redeliver')),
    revision integer NOT NULL,
    delivery_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,space_id,id),
    FOREIGN KEY (organization_id,space_id,subscription_id)
        REFERENCES space.webhook_subscriptions(organization_id,space_id,id)
);

DO $worker$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='space_webhook_worker') THEN
        CREATE ROLE space_webhook_worker NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB
            NOCREATEROLE NOREPLICATION NOBYPASSRLS;
        COMMENT ON ROLE space_webhook_worker IS 'space.webhooks.worker.v1';
    ELSIF (SELECT shobj_description(oid,'pg_authid') FROM pg_roles
           WHERE rolname='space_webhook_worker') IS DISTINCT FROM 'space.webhooks.worker.v1' THEN
        RAISE EXCEPTION 'Unrelated space_webhook_worker role exists';
    END IF;
END
$worker$;
GRANT USAGE ON SCHEMA space TO space_webhook_worker;
GRANT EXECUTE ON FUNCTION space.check_environment(uuid) TO space_webhook_worker;
GRANT SELECT ON space.webhook_subscriptions,space.webhook_events,
    space.webhook_deliveries,space.webhook_attempts TO space_webhook_worker;
-- Parameters: subscription shared-lock acquisition during network I/O.
-- What it does: supplies the column UPDATE privilege PostgreSQL requires for FOR SHARE.
-- Output: the worker cannot change pause, removal, destination or secret settings.
GRANT UPDATE (updated_at) ON space.webhook_subscriptions TO space_webhook_worker;
GRANT UPDATE (status,attempts,cycle,available_at,lease_id,lease_until,status_code,error_code,updated_at)
    ON space.webhook_deliveries TO space_webhook_worker;
GRANT INSERT,UPDATE ON space.webhook_attempts TO space_webhook_worker;
GRANT SELECT,INSERT,UPDATE ON space.webhook_subscriptions,space.webhook_commands TO space_app;
GRANT SELECT,INSERT ON space.webhook_events,space.webhook_deliveries TO space_app;
GRANT SELECT ON space.webhook_attempts TO space_app;
GRANT UPDATE (status,attempts,cycle,available_at,lease_id,lease_until,status_code,error_code,updated_at)
    ON space.webhook_deliveries TO space_app;

DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['webhook_subscriptions','webhook_events','webhook_deliveries',
                             'webhook_attempts','webhook_commands'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('REVOKE ALL ON space.%I FROM PUBLIC',t);
        EXECUTE format('CREATE POLICY owner_read ON space.%I FOR SELECT TO space_app USING (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND space.space_role(organization_id,space_id)=''owner'')',t);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I
            USING (true) WITH CHECK (true)',t,current_user);
        IF t!='webhook_commands' THEN
            EXECUTE format('CREATE POLICY delivery_worker ON space.%I TO space_webhook_worker
                USING (true) WITH CHECK (true)',t);
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['webhook_subscriptions','webhook_commands','webhook_deliveries'] LOOP
        EXECUTE format('CREATE POLICY owner_update ON space.%I FOR UPDATE TO space_app USING (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND space.space_role(organization_id,space_id)=''owner'') WITH CHECK (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND space.space_role(organization_id,space_id)=''owner'')',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['webhook_subscriptions','webhook_commands'] LOOP
        EXECUTE format('CREATE POLICY owner_insert ON space.%I FOR INSERT TO space_app WITH CHECK (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND space.space_role(organization_id,space_id)=''owner'')',t);
    END LOOP;
    FOREACH t IN ARRAY ARRAY['webhook_events','webhook_deliveries'] LOOP
        EXECUTE format('CREATE POLICY writer_insert ON space.%I FOR INSERT TO space_app WITH CHECK (
            organization_id=space.requested_organization_id() AND space_id=space.requested_space_id()
            AND space.space_role(organization_id,space_id) IN (''owner'',''admin'',''member''))',t);
    END LOOP;
END
$policies$;

-- Parameters: authenticated Space write scope and selected database.
-- What it does: returns only encrypted filters, never endpoints or signing secrets, to record writers.
-- Output: enabled subscriptions for the transactional outbox.
CREATE FUNCTION space.active_webhook_subscriptions(selected_database uuid)
RETURNS TABLE(id uuid,revision integer,key_id uuid,encrypted_filter bytea)
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    SELECT s.id,s.revision,s.key_id,s.encrypted_filter FROM space.webhook_subscriptions s
    WHERE s.organization_id=space.requested_organization_id() AND s.space_id=space.requested_space_id()
    AND s.database_id=selected_database AND NOT s.paused AND s.removed_at IS NULL
    AND space.space_role(s.organization_id,s.space_id) IN ('owner','admin','member')
$$;
CREATE FUNCTION space.webhook_queue_size() RETURNS bigint
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,space,pg_temp AS $$
    SELECT count(*) FROM space.webhook_deliveries d WHERE
    d.organization_id=space.requested_organization_id() AND d.space_id=space.requested_space_id()
    AND d.status IN ('queued','processing')
    AND space.space_role(d.organization_id,d.space_id) IN ('owner','admin','member')
$$;
REVOKE ALL ON FUNCTION space.active_webhook_subscriptions(uuid),space.webhook_queue_size() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.active_webhook_subscriptions(uuid),space.webhook_queue_size() TO space_app;
