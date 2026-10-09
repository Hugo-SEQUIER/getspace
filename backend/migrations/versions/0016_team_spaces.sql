ALTER TABLE space.organizations DROP CONSTRAINT organizations_owner_principal_id_key;
ALTER TABLE space.organizations DROP CONSTRAINT organizations_kind_check;
ALTER TABLE space.organizations ADD CHECK (kind IN ('personal','team'));
CREATE UNIQUE INDEX one_personal_organization ON space.organizations(owner_principal_id) WHERE kind='personal';
-- Public routes select a Space by ID; a caller-supplied creation ID must never
-- collide with a Space in another organization and change the selected scope.
CREATE UNIQUE INDEX spaces_global_id ON space.spaces(id);

ALTER TABLE space.spaces ADD COLUMN owner_principal_id uuid REFERENCES space.principals(id);
UPDATE space.spaces s SET owner_principal_id=o.owner_principal_id FROM space.organizations o WHERE o.id=s.organization_id;
ALTER TABLE space.spaces ALTER COLUMN owner_principal_id SET NOT NULL;
ALTER TABLE space.spaces ALTER COLUMN owner_principal_id SET DEFAULT space.current_principal_id();
ALTER TABLE space.spaces ADD COLUMN visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','restricted','shared'));

CREATE TABLE space.organization_members (
    organization_id uuid NOT NULL REFERENCES space.organizations(id),
    principal_id uuid NOT NULL REFERENCES space.principals(id),
    id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    role text NOT NULL CHECK (role IN ('admin','member','viewer')),
    active boolean NOT NULL DEFAULT true,
    encrypted_profile bytea,
    key_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (organization_id,principal_id),
    CHECK (encrypted_profile IS NULL OR octet_length(encrypted_profile) BETWEEN 1 AND 16384)
);
CREATE TABLE space.space_members (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    principal_id uuid NOT NULL,
    role text NOT NULL CHECK (role IN ('member','viewer')),
    PRIMARY KEY (organization_id,space_id,principal_id),
    FOREIGN KEY (organization_id,space_id) REFERENCES space.spaces(organization_id,id),
    FOREIGN KEY (organization_id,principal_id) REFERENCES space.organization_members(organization_id,principal_id)
);
CREATE INDEX memberships_by_principal ON space.organization_members(principal_id,organization_id) WHERE active;
CREATE TABLE space.team_invitations (
    organization_id uuid NOT NULL REFERENCES space.organizations(id),
    id uuid PRIMARY KEY,
    token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash)=32),
    role text NOT NULL CHECK (role IN ('admin','member','viewer')),
    created_by uuid NOT NULL REFERENCES space.principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    accepted_by uuid REFERENCES space.principals(id),
    accepted_at timestamptz
);

-- These narrow helpers read as the migration owner to avoid recursive RLS.
-- Identity still comes exclusively from the verified transaction context.
CREATE FUNCTION space.organization_role(org uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE WHEN o.owner_principal_id=space.current_principal_id() THEN 'owner' ELSE m.role END
    FROM space.organizations o LEFT JOIN space.organization_members m
        ON m.organization_id=o.id AND m.principal_id=space.current_principal_id() AND m.active
    WHERE o.id=org
$$;
CREATE FUNCTION space.space_role(org uuid,sid uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT CASE
        WHEN space.organization_role(org) IS NULL THEN NULL
        WHEN s.owner_principal_id=space.current_principal_id() THEN CASE WHEN space.organization_role(org)='viewer' THEN 'viewer' ELSE 'owner' END
        WHEN s.visibility='shared' THEN space.organization_role(org)
        WHEN s.visibility='restricted' AND m.role IS NOT NULL THEN
            CASE WHEN space.organization_role(org)='viewer' THEN 'viewer' ELSE m.role END
        ELSE NULL END
    FROM space.spaces s LEFT JOIN space.space_members m ON m.organization_id=s.organization_id
        AND m.space_id=s.id AND m.principal_id=space.current_principal_id()
    WHERE s.organization_id=org AND s.id=sid
$$;
CREATE FUNCTION space.accessible_spaces() RETURNS TABLE(organization_id uuid,space_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT s.organization_id,s.id FROM space.spaces s JOIN (
        SELECT o.id FROM space.organizations o WHERE o.owner_principal_id=space.current_principal_id()
        UNION SELECT m.organization_id FROM space.organization_members m WHERE m.principal_id=space.current_principal_id() AND m.active
    ) own ON own.id=s.organization_id WHERE space.space_role(s.organization_id,s.id) IS NOT NULL
$$;
CREATE FUNCTION space.resolve_space(sid uuid) RETURNS TABLE(organization_id uuid,space_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT s.organization_id,s.id FROM space.spaces s WHERE s.id=coalesce(sid,(
        SELECT o.default_space_id FROM space.organizations o
        WHERE o.kind='personal' AND o.owner_principal_id=space.current_principal_id()
    )) AND space.space_role(s.organization_id,s.id) IS NOT NULL
$$;
CREATE FUNCTION space.connection_space(client uuid,grant_id uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    SELECT g.space_id FROM space.oauth_grants g WHERE g.principal_id=space.current_principal_id()
        AND g.client_id=client AND g.id=grant_id AND g.revoked_at IS NULL
$$;
DROP POLICY personal_owner ON space.organizations;
CREATE POLICY organization_read ON space.organizations FOR SELECT TO space_app USING (space.organization_role(id) IS NOT NULL);
CREATE POLICY organization_create ON space.organizations FOR INSERT TO space_app WITH CHECK (owner_principal_id=space.current_principal_id());
DROP POLICY private_space_owner ON space.spaces;
CREATE POLICY space_access ON space.spaces TO space_app USING (
    organization_id=space.requested_organization_id() AND id=space.requested_space_id()
    AND space.space_role(organization_id,id) IS NOT NULL
) WITH CHECK (
    organization_id=space.requested_organization_id() AND id=space.requested_space_id()
    AND space.organization_role(organization_id) IN ('owner','admin','member')
    AND (space.space_role(organization_id,id) IN ('owner','admin','member') OR owner_principal_id=space.current_principal_id())
);

DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['organization_members','space_members','team_invitations'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY',t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY',t);
        EXECUTE format('CREATE POLICY maintenance_owner ON space.%I TO %I USING (true) WITH CHECK (true)',t,current_user);
    END LOOP;
    -- Existing scoped policies retain isolation; these restrictions add write roles.
    FOREACH t IN ARRAY ARRAY['collections','resources','resource_revisions','commands','events',
        'memory_claims','resource_terms','memory_jobs','agent_identities','workspace_items','workspace_item_revisions',
        'workspace_item_commands','restore_sessions','restore_chunks','restore_objects','space_keys'] LOOP
        EXECUTE format('CREATE POLICY member_insert ON space.%I AS RESTRICTIVE FOR INSERT TO space_app WITH CHECK (space.space_role(organization_id,space_id) IN (''owner'',''admin'',''member''))',t);
        EXECUTE format('CREATE POLICY member_update ON space.%I AS RESTRICTIVE FOR UPDATE TO space_app USING (space.space_role(organization_id,space_id) IN (''owner'',''admin'',''member'')) WITH CHECK (space.space_role(organization_id,space_id) IN (''owner'',''admin'',''member''))',t);
        EXECUTE format('CREATE POLICY member_delete ON space.%I AS RESTRICTIVE FOR DELETE TO space_app USING (space.space_role(organization_id,space_id) IN (''owner'',''admin'',''member''))',t);
    END LOOP;
END
$policies$;
CREATE POLICY member_read ON space.organization_members FOR SELECT TO space_app USING (space.organization_role(organization_id) IS NOT NULL);
CREATE POLICY member_create ON space.organization_members FOR INSERT TO space_app WITH CHECK (space.organization_role(organization_id)='owner');
CREATE POLICY member_update ON space.organization_members FOR UPDATE TO space_app USING (principal_id=space.current_principal_id());
-- Role/status updates happen only through checked functions below.
GRANT SELECT,INSERT ON space.organization_members TO space_app;
GRANT UPDATE (encrypted_profile,key_id) ON space.organization_members TO space_app;
CREATE POLICY selected_member_read ON space.space_members FOR SELECT TO space_app USING (space.space_role(organization_id,space_id) IS NOT NULL);
CREATE POLICY selected_member_write ON space.space_members TO space_app USING (space.space_role(organization_id,space_id)='owner') WITH CHECK (space.space_role(organization_id,space_id)='owner');
GRANT SELECT,INSERT,DELETE ON space.space_members TO space_app;
CREATE POLICY invitation_admin ON space.team_invitations TO space_app USING (space.organization_role(organization_id) IN ('owner','admin')) WITH CHECK (
    space.organization_role(organization_id)='owner' OR (space.organization_role(organization_id)='admin' AND role IN ('member','viewer'))
);
GRANT SELECT,INSERT ON space.team_invitations TO space_app;
GRANT UPDATE (revoked_at) ON space.team_invitations TO space_app;
CREATE POLICY own_connection ON space.oauth_grants AS RESTRICTIVE TO space_app USING (principal_id=space.current_principal_id()) WITH CHECK (principal_id=space.current_principal_id());

CREATE FUNCTION space.accept_team_invitation(digest bytea) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE invitation space.team_invitations; actor uuid:=space.current_principal_id();
BEGIN
    IF actor IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO invitation FROM space.team_invitations WHERE token_hash=digest;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(invitation.organization_id::text,16));
    SELECT * INTO invitation FROM space.team_invitations WHERE token_hash=digest FOR UPDATE;
    IF invitation.revoked_at IS NOT NULL OR invitation.expires_at<=now() THEN RETURN NULL; END IF;
    IF invitation.accepted_by IS NOT NULL THEN
        IF invitation.accepted_by=actor AND space.organization_role(invitation.organization_id) IS NOT NULL THEN RETURN invitation.organization_id; END IF;
        RETURN NULL;
    END IF;
    -- Existing membership never gains a higher role from redeeming another link.
    INSERT INTO space.organization_members(organization_id,principal_id,role)
        VALUES(invitation.organization_id,actor,invitation.role)
        ON CONFLICT (organization_id,principal_id) DO UPDATE SET
            role=CASE WHEN organization_members.active THEN organization_members.role ELSE excluded.role END,active=true;
    UPDATE space.team_invitations SET accepted_by=actor,accepted_at=now() WHERE id=invitation.id;
    RETURN invitation.organization_id;
END
$$;
CREATE FUNCTION space.change_team_member(org uuid,member uuid,new_role text) RETURNS boolean
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
    UPDATE space.team_invitations SET revoked_at=now() WHERE organization_id=org AND created_by=target.principal_id AND accepted_at IS NULL;
    IF new_role IS NULL THEN DELETE FROM space.space_members WHERE organization_id=org AND principal_id=target.principal_id; END IF;
    RETURN true;
END
$$;
CREATE FUNCTION space.set_connection_grant(client uuid,resource_url text,writable boolean,agent uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE org uuid:=space.requested_organization_id(); sid uuid:=space.requested_space_id(); role text;
BEGIN
    role:=space.space_role(org,sid);
    IF role IS NULL OR (writable AND role='viewer') THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    IF agent IS NOT NULL AND NOT EXISTS (SELECT 1 FROM space.agent_identities WHERE organization_id=org AND space_id=sid AND id=agent) THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    INSERT INTO space.oauth_grants(organization_id,space_id,principal_id,client_id,id,resource,can_write,agent_id)
        VALUES(org,sid,space.current_principal_id(),client,gen_random_uuid(),resource_url,writable,agent)
        ON CONFLICT(principal_id,client_id) DO UPDATE SET organization_id=excluded.organization_id,space_id=excluded.space_id,
            id=excluded.id,resource=excluded.resource,can_write=excluded.can_write,revoked_at=NULL,created_at=now(),
            agent_id=CASE WHEN oauth_grants.space_id=excluded.space_id THEN coalesce(excluded.agent_id,oauth_grants.agent_id) ELSE excluded.agent_id END;
END
$$;

-- Model read/recall is available to viewers too, within their visible Space.
CREATE OR REPLACE FUNCTION space.consume_model_request() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE org uuid:=space.requested_organization_id(); sid uuid:=space.requested_space_id();
    current_month date:=date_trunc('month',now() AT TIME ZONE 'UTC')::date; used integer;
BEGIN
    IF space.space_role(org,sid) IS NULL THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    INSERT INTO space.usage_limits(organization_id,space_id) VALUES(org,sid) ON CONFLICT DO NOTHING;
    UPDATE space.usage_limits SET month=current_month,model_requests=CASE WHEN month=current_month THEN model_requests+1 ELSE 1 END
        WHERE organization_id=org AND space_id=sid AND (CASE WHEN month=current_month THEN model_requests ELSE 0 END)<model_request_limit RETURNING model_requests INTO used;
    IF used IS NULL THEN RAISE EXCEPTION 'space.model_quota'; END IF;
    RETURN used;
END
$$;
REVOKE ALL ON FUNCTION space.organization_role(uuid),space.space_role(uuid,uuid),space.accessible_spaces(),space.resolve_space(uuid),
    space.connection_space(uuid,uuid),space.accept_team_invitation(bytea),space.change_team_member(uuid,uuid,text),space.set_connection_grant(uuid,text,boolean,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.organization_role(uuid),space.space_role(uuid,uuid),space.accessible_spaces(),space.resolve_space(uuid),
    space.connection_space(uuid,uuid),space.accept_team_invitation(bytea),space.change_team_member(uuid,uuid,text),space.set_connection_grant(uuid,text,boolean,uuid) TO space_app;

GRANT UPDATE (visibility) ON space.spaces TO space_app;
CREATE FUNCTION space.check_visibility_change() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
    IF OLD.visibility IS DISTINCT FROM NEW.visibility AND (
        OLD.owner_principal_id IS DISTINCT FROM space.current_principal_id()
        OR EXISTS (SELECT 1 FROM space.organizations o WHERE o.id=OLD.organization_id AND o.default_space_id=OLD.id)
    ) THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION space.check_visibility_change() FROM PUBLIC;
CREATE TRIGGER check_visibility_change BEFORE UPDATE OF visibility ON space.spaces
    FOR EACH ROW EXECUTE FUNCTION space.check_visibility_change();
CREATE FUNCTION space.revoke_space_connections(org uuid,sid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
    IF space.space_role(org,sid) IS DISTINCT FROM 'owner' THEN RAISE EXCEPTION 'space.invalid_scope'; END IF;
    UPDATE space.oauth_grants SET revoked_at=now() WHERE organization_id=org AND space_id=sid;
END
$$;
REVOKE ALL ON FUNCTION space.revoke_space_connections(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION space.revoke_space_connections(uuid,uuid) TO space_app;
