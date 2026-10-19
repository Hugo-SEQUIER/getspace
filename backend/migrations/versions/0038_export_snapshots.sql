-- An export spans many requests while agents keep writing. Each new revision records the
-- Space content revision it commits under, so an export reads exactly the revisions that
-- existed at its first page. Existing rows stay NULL: they committed before any export
-- could rely on the stamp, so every export includes them.
ALTER TABLE space.resource_revisions ADD COLUMN IF NOT EXISTS space_revision bigint;
ALTER TABLE space.workspace_item_revisions ADD COLUMN IF NOT EXISTS space_revision bigint;

-- Every revision writer bumps content_revision in the same transaction. A bump that came
-- first leaves its value in space.content_revision as "<space id>:<revision>"; otherwise
-- the Space row is locked here and the coming bump commits the next value. Either way
-- the stamp is above any snapshot read before the commit and at most the committed value.
CREATE OR REPLACE FUNCTION space.stamp_space_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE
    bumped text := current_setting('space.content_revision', true);
BEGIN
    IF coalesce(bumped, '') <> '' AND split_part(bumped, ':', 1) = NEW.space_id::text THEN
        NEW.space_revision := split_part(bumped, ':', 2)::bigint;
    ELSE
        SELECT s.content_revision + 1 INTO NEW.space_revision FROM space.spaces s
        WHERE s.organization_id = NEW.organization_id AND s.id = NEW.space_id FOR UPDATE;
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION space.stamp_space_revision() FROM PUBLIC;
DROP TRIGGER IF EXISTS stamp_space_revision ON space.resource_revisions;
CREATE TRIGGER stamp_space_revision BEFORE INSERT ON space.resource_revisions
    FOR EACH ROW EXECUTE FUNCTION space.stamp_space_revision();
DROP TRIGGER IF EXISTS stamp_space_revision ON space.workspace_item_revisions;
CREATE TRIGGER stamp_space_revision BEFORE INSERT ON space.workspace_item_revisions
    FOR EACH ROW EXECUTE FUNCTION space.stamp_space_revision();

-- Recall vectors live in memory_claims for the current claims only. An edit, a forget
-- or a re-embedding deletes a claim's row, and this trigger keeps its vector. An export
-- that started before the change still finds the vector of the claim text it exports.
-- Rows are matched by text hash and kept for one day, long enough for any export: each
-- retirement drops its memory's older rows and `make prune` drops the rest. Permanent
-- deletion of the memory and a Space purge remove them at once.
CREATE TABLE IF NOT EXISTS space.retired_claim_vectors (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    text_hash bytea NOT NULL CHECK (octet_length(text_hash) = 32),
    -- No fixed width: the installer sizes memory_claims.embedding to the configured
    -- embedding model, and the trigger below copies those vectors unchanged.
    embedding space.vector NOT NULL,
    embedding_model text NOT NULL CHECK (length(embedding_model) BETWEEN 1 AND 200),
    retired_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS retired_claim_vectors_claim
    ON space.retired_claim_vectors(organization_id, space_id, memory_id, claim_id);
CREATE INDEX IF NOT EXISTS retired_claim_vectors_age ON space.retired_claim_vectors(retired_at);
ALTER TABLE space.retired_claim_vectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE space.retired_claim_vectors FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS private_owner ON space.retired_claim_vectors;
CREATE POLICY private_owner ON space.retired_claim_vectors TO space_app USING (
    organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
) WITH CHECK (
    organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
);
DROP POLICY IF EXISTS maintenance_owner ON space.retired_claim_vectors;
CREATE POLICY maintenance_owner ON space.retired_claim_vectors TO CURRENT_USER
    USING (true) WITH CHECK (true);
GRANT SELECT, INSERT, DELETE ON space.retired_claim_vectors TO space_app;

CREATE OR REPLACE FUNCTION space.retire_claim_vector() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
    DELETE FROM space.retired_claim_vectors WHERE organization_id = OLD.organization_id
        AND space_id = OLD.space_id AND memory_id = OLD.memory_id
        AND retired_at < now() - interval '1 day';
    INSERT INTO space.retired_claim_vectors(organization_id, space_id, memory_id, claim_id,
        text_hash, embedding, embedding_model)
    VALUES (OLD.organization_id, OLD.space_id, OLD.memory_id, OLD.claim_id, OLD.text_hash,
        OLD.embedding, OLD.embedding_model);
    RETURN OLD;
END
$$;
REVOKE ALL ON FUNCTION space.retire_claim_vector() FROM PUBLIC;
DROP TRIGGER IF EXISTS retire_claim_vector ON space.memory_claims;
CREATE TRIGGER retire_claim_vector AFTER DELETE ON space.memory_claims
    FOR EACH ROW EXECUTE FUNCTION space.retire_claim_vector();
