-- Only a request that can still be answered counts toward the pending caps: an expired one
-- waited an hour for the sweep and kept new connections out meanwhile. The owner's direct
-- connection (POST /v1/agent-tokens) is not held by the device cap at all: it is approved as
-- it is asked, and a queue anyone can fill must not keep the owner out.

CREATE OR REPLACE FUNCTION space.oauth_begin(
    client uuid, redirect text, explicit boolean, challenge text, request_state text,
    requested_scopes text, target text, ttl interval
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE request uuid;
BEGIN
    PERFORM space.oauth_sweep();
    IF (SELECT count(*) FROM space.oauth_requests r
        WHERE r.status = 'pending' AND r.expires_at > now()) >= 10000 THEN
        RAISE EXCEPTION 'space.oauth_busy';
    END IF;
    INSERT INTO space.oauth_requests(client_id, redirect_uri, redirect_uri_explicit,
                                     code_challenge, state, scopes, resource, expires_at)
        VALUES (client, redirect, explicit, challenge, request_state, requested_scopes, target,
                now() + ttl)
        RETURNING id INTO request;
    RETURN request;
END
$$;

DROP FUNCTION space.device_begin(bytea, text, text, text, interval);

-- Parameters: digest of the agent's secret, the six-digit code, the agent's name and
-- harness, the code lifetime, and whether the pending cap applies (false only for the
-- owner's direct connection). Registers a pending request and sweeps finished ones.
CREATE FUNCTION space.device_begin(
    device_hash bytea, code text, client text, agent_harness text, ttl interval,
    capped boolean
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    -- A redeemed row is the token's record and stays until the token expires.
    DELETE FROM space.device_authorizations d
        WHERE (d.status IN ('pending', 'approved', 'denied')
               AND d.expires_at < now() - interval '1 hour')
           OR (d.status = 'redeemed' AND d.token_expires_at < now());
    DELETE FROM space.device_code_attempts a WHERE a.attempted_at < now() - interval '1 day';
    IF capped AND (SELECT count(*) FROM space.device_authorizations d
                   WHERE d.status = 'pending' AND d.expires_at > now()) >= 10000
    THEN
        RAISE EXCEPTION 'space.device_busy';
    END IF;
    INSERT INTO space.device_authorizations(device_code_hash, user_code, client_name, harness,
                                            expires_at)
        VALUES (device_hash, code, client, agent_harness, now() + ttl);
END
$$;

REVOKE ALL ON FUNCTION space.device_begin(bytea, text, text, text, interval, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.device_begin(bytea, text, text, text, interval, boolean)
    TO space_app;
