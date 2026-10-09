-- A searchable fingerprint of every resource revision, beside the encrypted original.
--
-- Why: a document was reachable only by decrypting the metadata of every resource in the
-- Space on every recall. That per-recall cost is what capped a Space at 512 resources.
-- This table holds the same words as keyed HMAC fingerprints, so the database can select
-- candidate resources and the application decrypts only the ones it will show.
--
-- Readable here: the fingerprint of a word, how often it occurs in one field, and how
-- many tokens that field holds. No word is readable, and a fingerprint cannot be turned
-- back into a word without the Space key. The fingerprint key is an HKDF subkey of the
-- Space key (see src/space/terms.py), so a database export alone reveals only that two
-- resources share a word. Rotating the Space key requires re-fingerprinting the Space.
--
-- Field 0 is the searchable metadata (title and topic labels); field 1 is the body.
-- Only the current revision of a resource is stored here; a new revision replaces it.
SET LOCAL search_path = space, public;

CREATE TABLE space.resource_terms (
    organization_id uuid NOT NULL,
    space_id uuid NOT NULL,
    resource_id uuid NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    field smallint NOT NULL CHECK (field IN (0, 1)),
    terms bytea[] NOT NULL,
    occurrences smallint[] NOT NULL,
    tokens integer NOT NULL CHECK (tokens >= 0),
    PRIMARY KEY (organization_id, space_id, resource_id, revision, field),
    -- Derived data: deleting a resource takes its fingerprints with it.
    FOREIGN KEY (organization_id, space_id, resource_id)
        REFERENCES space.resources(organization_id, space_id, id) ON DELETE CASCADE,
    -- The arrays describe the same terms in the same order.
    CHECK (array_length(terms, 1) = array_length(occurrences, 1)),
    -- Must not exceed MAX_INDEXED_TERMS in src/space/terms.py; a test asserts the pair.
    CHECK (coalesce(array_length(terms, 1), 0) <= 512),
    CHECK (0 <= ALL (occurrences))
);

-- Candidate selection: resources whose metadata or body shares a query fingerprint.
-- The planner combines this with the scope index below as a bitmap AND.
CREATE INDEX resource_terms_match_idx ON space.resource_terms USING gin (terms);
-- Scope and revision cleanup.
CREATE INDEX resource_terms_scope_idx ON space.resource_terms
    (organization_id, space_id, resource_id);

DO $policies$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['resource_terms'] LOOP
        EXECUTE format('ALTER TABLE space.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE space.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('CREATE POLICY private_owner ON space.%I TO space_app USING (
            organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id = %I.organization_id AND s.id = %I.space_id)
        ) WITH CHECK (
            organization_id = space.requested_organization_id() AND space_id = space.requested_space_id()
            AND EXISTS (SELECT 1 FROM space.spaces s WHERE s.organization_id = %I.organization_id AND s.id = %I.space_id)
        )', t, t, t, t, t);
    END LOOP;
END
$policies$;

GRANT SELECT, INSERT, UPDATE, DELETE ON space.resource_terms TO space_app;
REVOKE ALL ON ALL TABLES IN SCHEMA space FROM PUBLIC;
