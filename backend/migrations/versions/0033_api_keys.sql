-- Personal API keys. A signed-in person creates a key for one Space so their own client
-- can call the REST API as them, read-only or read-write. Keys never administer access
-- and never reach MCP; the application enforces that. Keys are stored as SHA-256
-- digests beside a short display prefix. No content is stored here.
CREATE TABLE space.api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
    prefix text NOT NULL CHECK (prefix ~ '^spk_[A-Za-z0-9_-]{8}$'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    identity_issuer text NOT NULL CHECK (length(identity_issuer) BETWEEN 1 AND 2048),
    identity_subject text NOT NULL CHECK (length(identity_subject) BETWEEN 1 AND 512),
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    can_write boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    last_used_at timestamptz,
    FOREIGN KEY (organization_id, space_id) REFERENCES space.spaces(organization_id, id)
);
CREATE INDEX api_keys_owner ON space.api_keys (organization_id, space_id, principal_id);

-- The runtime role never touches these rows directly; the definer functions below
-- are its only path, and the migration owner keeps its maintenance access.
ALTER TABLE space.api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.api_keys FORCE ROW LEVEL SECURITY;
REVOKE ALL ON space.api_keys FROM PUBLIC, space_app;
DO $security$
BEGIN
    EXECUTE format('CREATE POLICY maintenance_owner ON space.api_keys TO %I '
        'USING (true) WITH CHECK (true)', current_user);
END
$security$;

-- Parameters: digest and display prefix of the new key, its name, write flag, expiry
-- (NULL for none) and the per-person active key budget. The Space comes from the scoped
-- transaction and the caller must still hold a role there. Returns the key's ID.
CREATE FUNCTION space.api_key_create(
    token bytea, key_prefix text, key_name text, writable boolean, valid_until timestamptz,
    max_active integer
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    me uuid := space.current_principal_id();
    org uuid := space.requested_organization_id();
    target uuid := space.requested_space_id();
    role text;
    created uuid;
BEGIN
    IF me IS NULL OR org IS NULL OR target IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    role := space.space_role(org, target);
    IF role IS NULL OR (writable AND role = 'viewer') THEN
        RAISE EXCEPTION 'space.invalid_scope';
    END IF;
    -- Serialize one person's creations in this Space so the budget holds under races.
    PERFORM pg_advisory_xact_lock(hashtextextended('api_keys:' || me::text || ':' || target::text, 0));
    DELETE FROM space.api_keys k
        WHERE k.principal_id = me AND k.space_id = target AND k.expires_at <= now();
    IF (SELECT count(*) FROM space.api_keys k
        WHERE k.principal_id = me AND k.space_id = target) >= max_active THEN
        RAISE EXCEPTION 'space.api_key_limit';
    END IF;
    INSERT INTO space.api_keys(token_hash, prefix, name, principal_id, identity_issuer,
                               identity_subject, organization_id, space_id, can_write, expires_at)
        VALUES (token, key_prefix, key_name, me,
                current_setting('space.identity_issuer'), current_setting('space.identity_subject'),
                org, target, writable, valid_until)
        RETURNING id INTO created;
    RETURN created;
END
$$;

-- Returns the caller's live keys in the scoped Space, newest first. Never the digest.
CREATE FUNCTION space.api_key_list()
RETURNS TABLE(id uuid, name text, prefix text, can_write boolean, created_at timestamptz,
              expires_at timestamptz, last_used_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT k.id, k.name, k.prefix, k.can_write, k.created_at, k.expires_at, k.last_used_at
    FROM space.api_keys k
    WHERE k.principal_id = space.current_principal_id()
      AND k.organization_id = space.requested_organization_id()
      AND k.space_id = space.requested_space_id()
      AND (k.expires_at IS NULL OR k.expires_at > now())
    ORDER BY k.created_at DESC, k.id
$$;

-- Parameters: one of the caller's keys in the scoped Space. Deletes it; returns whether
-- it existed. Revocation is immediate because every request looks the key up.
CREATE FUNCTION space.api_key_revoke(key uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE changed integer;
BEGIN
    DELETE FROM space.api_keys k
        WHERE k.id = key AND k.principal_id = space.current_principal_id()
          AND k.organization_id = space.requested_organization_id()
          AND k.space_id = space.requested_space_id();
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END
$$;

-- Parameters: digest of a presented key. Returns the identity and Space it stands for
-- while it is live, recording use at most once every five minutes.
CREATE FUNCTION space.api_key_identity(token bytea)
RETURNS TABLE(id uuid, issuer text, subject text, space_id uuid, can_write boolean,
              expires_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE live space.api_keys%ROWTYPE;
BEGIN
    SELECT * INTO live FROM space.api_keys k
        WHERE k.token_hash = token AND (k.expires_at IS NULL OR k.expires_at > now());
    IF NOT FOUND THEN RETURN; END IF;
    IF live.last_used_at IS NULL OR live.last_used_at < now() - interval '5 minutes' THEN
        UPDATE space.api_keys k SET last_used_at = now() WHERE k.id = live.id;
    END IF;
    RETURN QUERY SELECT live.id, live.identity_issuer, live.identity_subject, live.space_id,
        live.can_write, live.expires_at;
END
$$;

REVOKE ALL ON FUNCTION space.api_key_create(bytea, text, text, boolean, timestamptz, integer),
    space.api_key_list(),
    space.api_key_revoke(uuid),
    space.api_key_identity(bytea) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.api_key_create(bytea, text, text, boolean, timestamptz, integer),
    space.api_key_list(),
    space.api_key_revoke(uuid),
    space.api_key_identity(bytea) TO space_app;

-- Rejoining a team, or a changed role or Space access, must not reactivate old keys,
-- exactly as it already revokes delegated agent connections. Bodies from 0016 with the
-- key deletion added.
CREATE OR REPLACE FUNCTION space.change_team_member(org uuid,member uuid,new_role text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE actor_role text; target space.organization_members; owner_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(org::text,16));
    actor_role:=space.organization_role(org);
    SELECT * INTO target FROM space.organization_members WHERE organization_id=org AND id=member AND active FOR UPDATE;
    SELECT owner_principal_id INTO owner_id FROM space.organizations WHERE id=org AND kind='team';
    IF target.id IS NULL OR owner_id IS NULL OR target.principal_id=owner_id THEN RETURN false; END IF;
    IF NOT coalesce((actor_role='owner' OR (actor_role='admin' AND target.role IN ('member','viewer') AND (new_role IS NULL OR new_role IN ('member','viewer')))
        OR (new_role IS NULL AND target.principal_id=space.current_principal_id())),false) THEN RETURN false; END IF;
    IF new_role IS NOT NULL AND new_role NOT IN ('admin','member','viewer') THEN RETURN false; END IF;
    UPDATE space.organization_members SET active=(new_role IS NOT NULL),role=coalesce(new_role,role) WHERE id=member;
    -- Rejoining or regaining a role must not reactivate old delegated credentials.
    UPDATE space.oauth_grants SET revoked_at=now() WHERE organization_id=org AND principal_id=target.principal_id;
    DELETE FROM space.api_keys WHERE organization_id=org AND principal_id=target.principal_id;
    UPDATE space.team_invitations SET revoked_at=now() WHERE organization_id=org AND created_by=target.principal_id AND accepted_at IS NULL;
    IF new_role IS NULL THEN DELETE FROM space.space_members WHERE organization_id=org AND principal_id=target.principal_id; END IF;
    RETURN true;
END
$$;
CREATE OR REPLACE FUNCTION space.revoke_space_connections(org uuid,sid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF space.space_role(org,sid) IS DISTINCT FROM 'owner' THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    UPDATE space.oauth_grants SET revoked_at=now() WHERE organization_id=org AND space_id=sid;
    DELETE FROM space.api_keys WHERE organization_id=org AND space_id=sid;
END
$$;
