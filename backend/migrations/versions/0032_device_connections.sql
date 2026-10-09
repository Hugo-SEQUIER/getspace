-- Device-code connections. An agent that cannot receive a browser redirect asks for a
-- short code, the owner approves it on the website, and the agent collects a
-- Space-issued bearer token bound to an ordinary connection grant row.
-- Codes and tokens are stored as SHA-256 digests. No content is stored here.
CREATE TABLE space.device_authorizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_code_hash bytea NOT NULL UNIQUE CHECK (octet_length(device_code_hash) = 32),
    user_code text NOT NULL CHECK (user_code ~ '^[0-9]{6}$'),
    client_name text NOT NULL CHECK (length(client_name) BETWEEN 1 AND 80),
    harness text NOT NULL CHECK (length(harness) BETWEEN 1 AND 40),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'denied', 'redeemed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_polled_at timestamptz,
    identity_issuer text CHECK (length(identity_issuer) BETWEEN 1 AND 2048),
    identity_subject text CHECK (length(identity_subject) BETWEEN 1 AND 512),
    principal_id uuid REFERENCES space.principals(id),
    organization_id uuid,
    space_id uuid,
    client_id uuid,
    grant_id uuid,
    agent_id uuid,
    can_write boolean,
    decided_at timestamptz,
    token_hash bytea UNIQUE CHECK (octet_length(token_hash) = 32),
    token_expires_at timestamptz,
    redeemed_at timestamptz
);
-- One live code per six-digit value; finished rows free the value again.
CREATE UNIQUE INDEX device_authorizations_pending_code
    ON space.device_authorizations (user_code) WHERE status = 'pending';
CREATE INDEX device_authorizations_expiry ON space.device_authorizations (expires_at);

-- Wrong guesses per signed-in person; a six-digit space needs a guess budget.
CREATE TABLE space.device_code_attempts (
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    attempted_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX device_code_attempts_recent
    ON space.device_code_attempts (principal_id, attempted_at);

-- The runtime role never touches these rows directly; the definer functions below
-- are its only path, and the migration owner keeps its maintenance access.
DO $security$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['device_authorizations', 'device_code_attempts'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('REVOKE ALL ON space.%I FROM PUBLIC, space_app', table_name);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I '
            'USING (true) WITH CHECK (true)', table_name, current_user);
    END LOOP;
END
$security$;

-- Parameters: digest of the agent's secret, the six-digit code, the agent's name and
-- harness, and the code lifetime. Registers a pending request and sweeps finished ones.
CREATE FUNCTION space.device_begin(
    device_hash bytea, code text, client text, agent_harness text, ttl interval
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- A redeemed row is the token's record and stays until the token expires.
    DELETE FROM space.device_authorizations d
        WHERE (d.status IN ('pending', 'approved', 'denied')
               AND d.expires_at < now() - interval '1 hour')
           OR (d.status = 'redeemed' AND d.token_expires_at < now());
    DELETE FROM space.device_code_attempts a WHERE a.attempted_at < now() - interval '1 day';
    IF (SELECT count(*) FROM space.device_authorizations d WHERE d.status = 'pending') >= 10000
    THEN
        RAISE EXCEPTION 'space.device_busy';
    END IF;
    INSERT INTO space.device_authorizations(device_code_hash, user_code, client_name, harness,
                                            expires_at)
        VALUES (device_hash, code, client, agent_harness, now() + ttl);
END
$$;

-- Parameters: the six-digit code typed by the signed-in person. Binds a pending request
-- to that person and returns it; a wrong code counts against a fifteen-minute budget.
CREATE FUNCTION space.device_claim(code text)
RETURNS TABLE(authorization_id uuid, agent_name text, agent_harness text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE me uuid := space.current_principal_id(); request space.device_authorizations%ROWTYPE;
BEGIN
    IF me IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    IF (SELECT count(*) FROM space.device_code_attempts a
        WHERE a.principal_id = me AND a.attempted_at > now() - interval '15 minutes') >= 10
    THEN
        RAISE EXCEPTION 'space.device_attempts';
    END IF;
    SELECT * INTO request FROM space.device_authorizations d
        WHERE d.user_code = code AND d.status = 'pending' AND d.expires_at > now()
          AND (d.principal_id IS NULL OR d.principal_id = me)
        FOR UPDATE;
    IF NOT FOUND THEN
        INSERT INTO space.device_code_attempts(principal_id) VALUES (me);
        RETURN;
    END IF;
    UPDATE space.device_authorizations d SET principal_id = me,
        identity_issuer = nullif(current_setting('space.identity_issuer', true), ''),
        identity_subject = nullif(current_setting('space.identity_subject', true), '')
        WHERE d.id = request.id;
    DELETE FROM space.device_code_attempts a WHERE a.principal_id = me;
    RETURN QUERY SELECT request.id, request.client_name, request.harness;
END
$$;

-- Parameters: the claimed request, the decision, and for an approval the new grant's
-- client, grant row, agent identity and write flag. The Space comes from the scoped
-- transaction. Returns false when the request is not this person's pending one.
CREATE FUNCTION space.device_decide(
    request_id uuid, approve boolean, client uuid, grant_row uuid, agent uuid, writable boolean
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE me uuid := space.current_principal_id(); changed integer;
BEGIN
    IF me IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    IF approve AND (space.requested_organization_id() IS NULL OR space.requested_space_id() IS NULL
                    OR client IS NULL OR grant_row IS NULL) THEN
        RAISE EXCEPTION 'space.invalid_scope';
    END IF;
    UPDATE space.device_authorizations d SET
        status = CASE WHEN approve THEN 'approved' ELSE 'denied' END,
        decided_at = now(),
        organization_id = CASE WHEN approve THEN space.requested_organization_id() END,
        space_id = CASE WHEN approve THEN space.requested_space_id() END,
        client_id = CASE WHEN approve THEN client END,
        grant_id = CASE WHEN approve THEN grant_row END,
        agent_id = CASE WHEN approve THEN agent END,
        can_write = CASE WHEN approve THEN writable END,
        -- The agent has a fresh window to collect the decision.
        expires_at = now() + interval '10 minutes'
        WHERE d.id = request_id AND d.principal_id = me AND d.status = 'pending'
          AND d.expires_at > now();
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

-- Parameters: digest of the agent's secret, digest of the token to issue on approval,
-- the token lifetime and the minimum spacing between polls. Returns the outcome; an
-- approval is redeemed exactly once.
CREATE FUNCTION space.device_poll(
    device_hash bytea, token bytea, token_ttl interval, min_interval interval
) RETURNS TABLE(outcome text, agent uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE request space.device_authorizations%ROWTYPE;
BEGIN
    SELECT * INTO request FROM space.device_authorizations d
        WHERE d.device_code_hash = device_hash FOR UPDATE;
    IF NOT FOUND OR request.status = 'redeemed' OR request.expires_at <= now() THEN
        RETURN QUERY SELECT 'expired'::text, NULL::uuid;
        RETURN;
    END IF;
    IF request.last_polled_at IS NOT NULL AND request.last_polled_at > now() - min_interval THEN
        RETURN QUERY SELECT 'slow_down'::text, NULL::uuid;
        RETURN;
    END IF;
    UPDATE space.device_authorizations d SET last_polled_at = now() WHERE d.id = request.id;
    IF request.status = 'pending' THEN
        RETURN QUERY SELECT 'pending'::text, NULL::uuid;
        RETURN;
    END IF;
    IF request.status = 'denied' THEN
        DELETE FROM space.device_authorizations d WHERE d.id = request.id;
        RETURN QUERY SELECT 'denied'::text, NULL::uuid;
        RETURN;
    END IF;
    UPDATE space.device_authorizations d SET status = 'redeemed', redeemed_at = now(),
        token_hash = token, token_expires_at = now() + token_ttl
        WHERE d.id = request.id;
    RETURN QUERY SELECT 'approved'::text, request.agent_id;
END
$$;

-- Parameters: digest of a presented bearer token. Returns the identity it stands for
-- while the token is live and its grant is not revoked.
CREATE FUNCTION space.device_token_identity(token bytea)
RETURNS TABLE(issuer text, subject text, client uuid, grant_row uuid, agent uuid,
              valid_until timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT d.identity_issuer, d.identity_subject, d.client_id, d.grant_id, d.agent_id,
           d.token_expires_at
    FROM space.device_authorizations d
    JOIN space.oauth_grants g
      ON g.id = d.grant_id AND g.client_id = d.client_id AND g.revoked_at IS NULL
    WHERE d.token_hash = token AND d.status = 'redeemed' AND d.token_expires_at > now()
$$;

REVOKE ALL ON FUNCTION space.device_begin(bytea, text, text, text, interval),
    space.device_claim(text),
    space.device_decide(uuid, boolean, uuid, uuid, uuid, boolean),
    space.device_poll(bytea, bytea, interval, interval),
    space.device_token_identity(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.device_begin(bytea, text, text, text, interval),
    space.device_claim(text),
    space.device_decide(uuid, boolean, uuid, uuid, uuid, boolean),
    space.device_poll(bytea, bytea, interval, interval),
    space.device_token_identity(bytea) TO space_app;
