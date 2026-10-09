-- OAuth for MCP clients that connect through a browser, such as hosted assistants that
-- accept a server URL but no bearer header. A client registers itself (RFC 7591), sends
-- the owner's browser to the authorization endpoint, and the owner approves it on the
-- website. The client then exchanges a one-time code, with its PKCE verifier, for an
-- access token and a rotating refresh token. Both stand for an ordinary connection grant,
-- so listing, attribution and revocation work as for every other connection.
-- Client secrets, codes and tokens are stored as SHA-256 digests. No content is stored.
CREATE TABLE space.oauth_clients (
    id uuid PRIMARY KEY,
    secret_hash bytea CHECK (octet_length(secret_hash) = 32),
    -- The registered client metadata without its secret, as the registration returned it.
    metadata jsonb NOT NULL
        CHECK (jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 16384),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE space.oauth_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES space.oauth_clients(id) ON DELETE CASCADE,
    redirect_uri text NOT NULL CHECK (length(redirect_uri) BETWEEN 1 AND 2048),
    redirect_uri_explicit boolean NOT NULL,
    code_challenge text NOT NULL CHECK (code_challenge ~ '^[A-Za-z0-9_-]{43}$'),
    state text CHECK (length(state) <= 2048),
    scopes text NOT NULL DEFAULT '' CHECK (length(scopes) <= 1024),
    resource text CHECK (length(resource) <= 2048),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'redeemed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- Consent window while pending; the code's lifetime once approved.
    expires_at timestamptz NOT NULL,
    identity_issuer text CHECK (length(identity_issuer) BETWEEN 1 AND 2048),
    identity_subject text CHECK (length(identity_subject) BETWEEN 1 AND 512),
    principal_id uuid REFERENCES space.principals(id),
    grant_id uuid,
    code_hash bytea UNIQUE CHECK (octet_length(code_hash) = 32),
    -- The tokens a redeemed code produced, revoked if the code is presented again.
    token_id uuid
);
CREATE INDEX oauth_requests_expiry ON space.oauth_requests (expires_at);
CREATE INDEX oauth_requests_client ON space.oauth_requests (client_id);

CREATE TABLE space.oauth_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES space.oauth_clients(id) ON DELETE CASCADE,
    -- No foreign key: approving the client again gives its grant a new ID, which must
    -- leave these tokens behind rather than carry them over.
    grant_id uuid NOT NULL,
    identity_issuer text NOT NULL CHECK (length(identity_issuer) BETWEEN 1 AND 2048),
    identity_subject text NOT NULL CHECK (length(identity_subject) BETWEEN 1 AND 512),
    scopes text NOT NULL DEFAULT '' CHECK (length(scopes) <= 1024),
    resource text CHECK (length(resource) <= 2048),
    access_hash bytea NOT NULL UNIQUE CHECK (octet_length(access_hash) = 32),
    access_expires_at timestamptz NOT NULL,
    refresh_hash bytea NOT NULL UNIQUE CHECK (octet_length(refresh_hash) = 32),
    refresh_expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX oauth_tokens_client ON space.oauth_tokens (client_id);
CREATE INDEX oauth_tokens_expiry ON space.oauth_tokens (refresh_expires_at);

-- The runtime role never touches these rows directly; the definer functions below
-- are its only path, and the migration owner keeps its maintenance access.
DO $security$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['oauth_clients', 'oauth_requests', 'oauth_tokens'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format('REVOKE ALL ON space.%I FROM PUBLIC, space_app', table_name);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I '
            'USING (true) WITH CHECK (true)', table_name, current_user);
    END LOOP;
END
$security$;

-- Removes what no client can use any more: requests an hour past their window, tokens
-- past their refresh lifetime or whose connection was revoked or approved again, and
-- clients registered a day ago that never connected.
CREATE FUNCTION space.oauth_sweep() RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    DELETE FROM space.oauth_requests r WHERE r.expires_at < now() - interval '1 hour';
    DELETE FROM space.oauth_tokens t
        WHERE t.refresh_expires_at < now()
           OR NOT EXISTS (SELECT 1 FROM space.oauth_grants g
                          WHERE g.id = t.grant_id AND g.client_id = t.client_id
                            AND g.revoked_at IS NULL);
    DELETE FROM space.oauth_clients c
        WHERE c.created_at < now() - interval '1 day'
          AND NOT EXISTS (SELECT 1 FROM space.oauth_requests r WHERE r.client_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM space.oauth_tokens t WHERE t.client_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM space.oauth_grants g WHERE g.client_id = c.id);
$$;

-- Parameters: the new client's ID, the digest of its secret (null for a public client)
-- and its metadata. Registration is open, so unused registrations are bounded: past
-- 1000 clients the oldest one that never connected makes room.
CREATE FUNCTION space.oauth_register(client uuid, secret bytea, details jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE surplus integer;
BEGIN
    PERFORM space.oauth_sweep();
    surplus := (SELECT count(*) FROM space.oauth_clients) - 999;
    IF surplus > 0 THEN
        DELETE FROM space.oauth_clients c WHERE c.id IN (
            SELECT u.id FROM space.oauth_clients u
            WHERE NOT EXISTS (SELECT 1 FROM space.oauth_requests r WHERE r.client_id = u.id)
              AND NOT EXISTS (SELECT 1 FROM space.oauth_tokens t WHERE t.client_id = u.id)
              AND NOT EXISTS (SELECT 1 FROM space.oauth_grants g WHERE g.client_id = u.id)
            ORDER BY u.created_at LIMIT surplus);
    END IF;
    INSERT INTO space.oauth_clients(id, secret_hash, metadata) VALUES (client, secret, details);
END
$$;

-- Parameters: a client ID. Returns its secret digest and metadata.
CREATE FUNCTION space.oauth_client(client uuid) RETURNS TABLE(secret_hash bytea, details jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT c.secret_hash, c.metadata FROM space.oauth_clients c WHERE c.id = client
$$;

-- Parameters: a registered client's validated authorization request and how long the
-- owner has to answer it. Records the request and returns its ID for the consent page.
CREATE FUNCTION space.oauth_begin(
    client uuid, redirect text, explicit boolean, challenge text, request_state text,
    requested_scopes text, target text, ttl interval
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE request uuid;
BEGIN
    PERFORM space.oauth_sweep();
    IF (SELECT count(*) FROM space.oauth_requests r WHERE r.status = 'pending') >= 10000 THEN
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

-- Parameters: a request ID. Returns what the consent page shows while the request is
-- pending. Only a Space principal may read it.
CREATE FUNCTION space.oauth_request(request uuid)
RETURNS TABLE(client uuid, details jsonb, redirect text, requested_scopes text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF space.current_principal_id() IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    RETURN QUERY SELECT r.client_id, c.metadata, r.redirect_uri, r.scopes
        FROM space.oauth_requests r JOIN space.oauth_clients c ON c.id = r.client_id
        WHERE r.id = request AND r.status = 'pending' AND r.expires_at > now();
END
$$;

-- Parameters: the request, the decision and, for an approval, the digest of the code to
-- issue, the client's grant created in this transaction and the code's lifetime. A
-- denial deletes the request. Returns where to send the browser, or nothing when the
-- request is no longer pending.
CREATE FUNCTION space.oauth_decide(
    request uuid, approve boolean, code bytea, grant_row uuid, code_ttl interval
) RETURNS TABLE(redirect text, request_state text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE me uuid := space.current_principal_id(); pending space.oauth_requests%ROWTYPE;
BEGIN
    IF me IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    SELECT * INTO pending FROM space.oauth_requests r
        WHERE r.id = request AND r.status = 'pending' AND r.expires_at > now()
        FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF approve THEN
        IF code IS NULL OR NOT EXISTS (
            SELECT 1 FROM space.oauth_grants g
            WHERE g.id = grant_row AND g.client_id = pending.client_id
              AND g.principal_id = me AND g.revoked_at IS NULL
        ) THEN
            RAISE EXCEPTION 'space.invalid_scope';
        END IF;
        UPDATE space.oauth_requests r SET status = 'approved', principal_id = me,
            identity_issuer = nullif(current_setting('space.identity_issuer', true), ''),
            identity_subject = nullif(current_setting('space.identity_subject', true), ''),
            grant_id = grant_row, code_hash = code, expires_at = now() + code_ttl
            WHERE r.id = pending.id;
    ELSE
        DELETE FROM space.oauth_requests r WHERE r.id = pending.id;
    END IF;
    RETURN QUERY SELECT pending.redirect_uri, pending.state;
END
$$;

-- Parameters: digest of a presented code and the client presenting it. Returns the
-- approved request while the code is live. A code presented again after redemption
-- revokes the tokens it produced (RFC 6749, section 4.1.2) and returns nothing.
CREATE FUNCTION space.oauth_code(code bytea, client uuid)
RETURNS TABLE(redirect text, explicit boolean, challenge text, requested_scopes text,
              target text, valid_until timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE request space.oauth_requests%ROWTYPE;
BEGIN
    SELECT * INTO request FROM space.oauth_requests r
        WHERE r.code_hash = code AND r.client_id = client FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF request.status = 'redeemed' THEN
        DELETE FROM space.oauth_tokens t WHERE t.id = request.token_id;
        RETURN;
    END IF;
    IF request.status <> 'approved' OR request.expires_at <= now() THEN RETURN; END IF;
    RETURN QUERY SELECT request.redirect_uri, request.redirect_uri_explicit, request.code_challenge,
                        request.scopes, request.resource, request.expires_at;
END
$$;

-- Parameters: digest of the code, the client, and digests of the new access and refresh
-- tokens with their lifetimes. Redeems the code once and records the tokens while the
-- connection is live. Returns the granted scopes, or nothing when the code is spent.
CREATE FUNCTION space.oauth_redeem(
    code bytea, client uuid, access bytea, access_ttl interval, refresh bytea,
    refresh_ttl interval
) RETURNS TABLE(requested_scopes text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE request space.oauth_requests%ROWTYPE; issued uuid;
BEGIN
    SELECT * INTO request FROM space.oauth_requests r
        WHERE r.code_hash = code AND r.client_id = client FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    IF request.status = 'redeemed' THEN
        DELETE FROM space.oauth_tokens t WHERE t.id = request.token_id;
        RETURN;
    END IF;
    IF request.status <> 'approved' OR request.expires_at <= now() OR NOT EXISTS (
        SELECT 1 FROM space.oauth_grants g
        WHERE g.id = request.grant_id AND g.client_id = client AND g.revoked_at IS NULL
    ) THEN
        RETURN;
    END IF;
    INSERT INTO space.oauth_tokens(client_id, grant_id, identity_issuer, identity_subject,
                                   scopes, resource, access_hash, access_expires_at,
                                   refresh_hash, refresh_expires_at)
        VALUES (client, request.grant_id, request.identity_issuer, request.identity_subject,
                request.scopes, request.resource, access, now() + access_ttl, refresh,
                now() + refresh_ttl)
        RETURNING id INTO issued;
    UPDATE space.oauth_requests r SET status = 'redeemed', token_id = issued
        WHERE r.id = request.id;
    RETURN QUERY SELECT request.scopes;
END
$$;

-- Parameters: digest of a presented refresh token and the client presenting it. Returns
-- the token's scopes, resource and expiry while it and its connection are live.
CREATE FUNCTION space.oauth_refresh_token(refresh bytea, client uuid)
RETURNS TABLE(requested_scopes text, target text, valid_until timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT t.scopes, t.resource, t.refresh_expires_at
    FROM space.oauth_tokens t
    JOIN space.oauth_grants g
      ON g.id = t.grant_id AND g.client_id = t.client_id AND g.revoked_at IS NULL
    WHERE t.refresh_hash = refresh AND t.client_id = client AND t.refresh_expires_at > now()
$$;

-- Parameters: the presented refresh token's digest, the client, and digests of the
-- replacement pair with their lifetimes. Rotates both tokens at once, so the old pair
-- stops working. Returns false when the refresh token is no longer current.
CREATE FUNCTION space.oauth_rotate(
    refresh bytea, client uuid, access bytea, access_ttl interval, new_refresh bytea,
    refresh_ttl interval
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE changed integer;
BEGIN
    UPDATE space.oauth_tokens t SET access_hash = access, access_expires_at = now() + access_ttl,
        refresh_hash = new_refresh, refresh_expires_at = now() + refresh_ttl
        WHERE t.refresh_hash = refresh AND t.client_id = client AND t.refresh_expires_at > now()
          AND EXISTS (SELECT 1 FROM space.oauth_grants g
                      WHERE g.id = t.grant_id AND g.client_id = t.client_id
                        AND g.revoked_at IS NULL);
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

-- Parameters: digest of a presented access token. Returns the identity it stands for
-- while the token is live and its connection is not revoked.
CREATE FUNCTION space.oauth_token_identity(token bytea)
RETURNS TABLE(issuer text, subject text, client uuid, grant_row uuid, valid_until timestamptz,
              requested_scopes text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT t.identity_issuer, t.identity_subject, t.client_id, t.grant_id, t.access_expires_at,
           t.scopes
    FROM space.oauth_tokens t
    JOIN space.oauth_grants g
      ON g.id = t.grant_id AND g.client_id = t.client_id AND g.revoked_at IS NULL
    WHERE t.access_hash = token AND t.access_expires_at > now()
$$;

-- Parameters: digest of an access or refresh token and the client revoking it. Deletes
-- the pair (RFC 7009); an unknown token changes nothing.
CREATE FUNCTION space.oauth_revoke(token bytea, client uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
    DELETE FROM space.oauth_tokens t
    WHERE t.client_id = client AND (t.access_hash = token OR t.refresh_hash = token)
$$;

REVOKE ALL ON FUNCTION space.oauth_sweep(),
    space.oauth_register(uuid, bytea, jsonb),
    space.oauth_client(uuid),
    space.oauth_begin(uuid, text, boolean, text, text, text, text, interval),
    space.oauth_request(uuid),
    space.oauth_decide(uuid, boolean, bytea, uuid, interval),
    space.oauth_code(bytea, uuid),
    space.oauth_redeem(bytea, uuid, bytea, interval, bytea, interval),
    space.oauth_refresh_token(bytea, uuid),
    space.oauth_rotate(bytea, uuid, bytea, interval, bytea, interval),
    space.oauth_token_identity(bytea),
    space.oauth_revoke(bytea, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.oauth_register(uuid, bytea, jsonb),
    space.oauth_client(uuid),
    space.oauth_begin(uuid, text, boolean, text, text, text, text, interval),
    space.oauth_request(uuid),
    space.oauth_decide(uuid, boolean, bytea, uuid, interval),
    space.oauth_code(bytea, uuid),
    space.oauth_redeem(bytea, uuid, bytea, interval, bytea, interval),
    space.oauth_refresh_token(bytea, uuid),
    space.oauth_rotate(bytea, uuid, bytea, interval, bytea, interval),
    space.oauth_token_identity(bytea),
    space.oauth_revoke(bytea, uuid) TO space_app;
