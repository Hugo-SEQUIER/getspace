"""Parameters: scoped Workspace access and bounded recall candidates.
What it does: retrieves claims through keyed terms or explicit one-hop node links.
Output: virtual claim views; lexical and link ranks never masquerade as cosine scores.
"""

from space.memory import rank_lexical
from space.memory_pipeline import virtual_memories
from space.ranking import reciprocal_rank_fusion
from space.read_timing import read_stage
from space.terms import query_fingerprints, term_key

MAX_LEXICAL_RESOURCES = 32
MAX_LINK_SEEDS = 5
MAX_LINK_RESOURCES = 25

LEXICAL_SQL = (
    "WITH matches AS ("
    "SELECT DISTINCT t.resource_id, t.revision, u.term FROM space.resource_terms t "
    "JOIN space.resources r ON (r.organization_id,r.space_id,r.id,r.current_revision)="
    "(t.organization_id,t.space_id,t.resource_id,t.revision) "
    "JOIN LATERAL (SELECT unnest(t.terms) AS term) u ON TRUE "
    "WHERE t.space_id=%s AND r.memory_kind='memory' "
    "AND t.terms && %s::bytea[] AND u.term=ANY(%s::bytea[]) "
    "AND EXISTS (SELECT 1 FROM space.memory_claims c WHERE "
    "(c.organization_id,c.space_id,c.memory_id,c.revision)="
    "(r.organization_id,r.space_id,r.id,r.current_revision) "
    "AND (%s::text IS NULL OR c.embedding_model=%s))), "
    "rarity AS (SELECT term,count(*) AS resources FROM matches GROUP BY term), "
    "ranked AS (SELECT m.resource_id,m.revision,sum(1.0/r.resources) AS weight "
    "FROM matches m JOIN rarity r ON r.term=m.term GROUP BY m.resource_id,m.revision) "
    "SELECT resource_id,revision FROM ranked ORDER BY weight DESC,resource_id LIMIT %s"
)

LINKED_SQL = (
    "WITH seeds AS (SELECT r.id FROM space.resources r "
    "JOIN unnest(%s::uuid[],%s::integer[]) s(id,revision) "
    "ON (r.id,r.current_revision)=(s.id,s.revision) "
    "WHERE r.space_id=%s AND r.memory_kind='memory' "
    "AND EXISTS (SELECT 1 FROM space.memory_claims c WHERE "
    "(c.organization_id,c.space_id,c.memory_id,c.revision)="
    "(r.organization_id,r.space_id,r.id,r.current_revision) "
    "AND (%s::text IS NULL OR c.embedding_model=%s))), "
    "neighbors AS ("
    "SELECT l.target_memory_id AS resource_id,s.id AS seed "
    "FROM space.memory_node_links l JOIN seeds s ON l.source_memory_id=s.id "
    "WHERE l.space_id=%s UNION "
    "SELECT l.source_memory_id AS resource_id,s.id AS seed "
    "FROM space.memory_node_links l JOIN seeds s ON l.target_memory_id=s.id "
    "WHERE l.space_id=%s) "
    "SELECT n.resource_id,r.current_revision AS revision,"
    "array_agg(DISTINCT n.seed ORDER BY n.seed) AS linked_from "
    "FROM neighbors n JOIN space.resources r ON r.id=n.resource_id "
    "WHERE r.space_id=%s AND r.memory_kind='memory' "
    "AND NOT (r.id=ANY(%s::uuid[])) "
    "AND EXISTS (SELECT 1 FROM space.memory_claims c WHERE "
    "(c.organization_id,c.space_id,c.memory_id,c.revision)="
    "(r.organization_id,r.space_id,r.id,r.current_revision) "
    "AND (%s::text IS NULL OR c.embedding_model=%s)) "
    "GROUP BY n.resource_id,r.current_revision ORDER BY n.resource_id LIMIT %s"
)

CLAIMS_SQL = (
    "SELECT c.memory_id,c.claim_id,c.revision FROM space.memory_claims c "
    "JOIN space.resources r ON (r.organization_id,r.space_id,r.id,r.current_revision)="
    "(c.organization_id,c.space_id,c.memory_id,c.revision) "
    "WHERE c.space_id=%s AND c.memory_id=ANY(%s::uuid[]) AND r.memory_kind='memory' "
    "AND (%s::text IS NULL OR c.embedding_model=%s)"
)


def _selected_rows(workspace, connection, scope, space_id, rows, *, model_name):
    """Parameters: authorized transaction and bounded shortlist with exact revisions.
    What it does: reads encrypted metadata, key envelopes and current model claim IDs.
    Output: selected rows, keys and indexed claims; no plaintext or provider work.
    """
    if not rows:
        return [], {}, []
    expected = {str(row["resource_id"]): row["revision"] for row in rows}
    selected, key_rows = workspace._item_rows(
        connection, scope, space_id, list(expected), memory=True
    )
    selected = [row for row in selected if expected.get(str(row["resource_id"])) == row["revision"]]
    if not selected:
        return [], {}, []
    indexed = connection.execute(
        CLAIMS_SQL,
        (space_id, [row["resource_id"] for row in selected], model_name, model_name),
    ).fetchall()
    return selected, key_rows, indexed


def _hydrate(workspace, identity, space_id, selection, scope, *, cached_bodies=None):
    """Parameters: scoped encrypted selection and optional recall-local body cache.
    What it does: decrypts outside SQL and excludes metadata before reading claim bodies.
    Output: deduplicated virtual claim views restricted to current indexed membership.
    """
    rows, key_rows, indexed = selection
    if not rows:
        return []
    keys = workspace._unwrap_keys(scope, key_rows)
    items = workspace._summaries(rows, keys, scope, include_excluded=False)
    allowed = {}
    for row in indexed:
        allowed.setdefault((str(row["memory_id"]), row["revision"]), set()).add(
            str(row["claim_id"])
        )
    items = [item for item in items if (item["id"], item["revision"]) in allowed]
    contents = workspace._bodies(
        identity, space_id, items, keys, scope, cached_bodies=cached_bodies
    )
    views = {}
    for item in items:
        resource = item | {"_memory": contents[item["id"]]["memory"]}
        for view in virtual_memories(resource):
            if view["entry_id"] in allowed[(item["id"], item["revision"])]:
                views.setdefault(_claim_key(view), view)
    return [views[key] for key in sorted(views)]


@read_stage("recall_lexical")
def lexical(workspace, identity, space_id, query, limit, *, model_name=None, cached_bodies=None):
    """Parameters: scoped caller, query, result bound and optional embedding model.
    What it does: preselects at most 32 memories with keyed terms and BM25-ranks claims.
    Output: full virtual claim views with lexical origin, without synthetic cosine scores.
    """
    if limit <= 0 or not query:
        return []
    _, scoped = workspace._key(identity, space_id)
    terms = query_fingerprints(query, term_key(scoped.key.material))
    if not terms:
        return []
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            LEXICAL_SQL, (space_id, terms, terms, model_name, model_name, MAX_LEXICAL_RESOURCES)
        ).fetchall()
        selection = _selected_rows(
            workspace, connection, scope, space_id, rows, model_name=model_name
        )
    views = _hydrate(workspace, identity, space_id, selection, scope, cached_bodies=cached_bodies)
    return [view | {"candidate_origin": "lexical"} for view in _rank(views, query)[:limit]]


@read_stage("recall_lexical")
def linked(
    workspace, identity, space_id, seeds, query, limit, *, model_name=None, cached_bodies=None
):
    """Parameters: scoped caller, retrieved resource IDs, query and candidate bound.
    What it does: follows explicit node links once in either direction, never seed endpoints.
    Output: at most limit claims from 25 neighbors, including nonlexical linked evidence.
    """
    if limit <= 0 or not seeds:
        return []
    seed_ids = list(dict.fromkeys(str(seed) for seed in seeds))[:MAX_LINK_SEEDS]
    items, _, _ = workspace._items_for(identity, space_id, seed_ids, memory=True)
    if not items:
        return []
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            LINKED_SQL,
            (
                [item["id"] for item in items],
                [item["revision"] for item in items],
                space_id,
                model_name,
                model_name,
                space_id,
                space_id,
                space_id,
                seed_ids,
                model_name,
                model_name,
                MAX_LINK_RESOURCES,
            ),
        ).fetchall()
        selection = _selected_rows(
            workspace, connection, scope, space_id, rows, model_name=model_name
        )
    views = _hydrate(workspace, identity, space_id, selection, scope, cached_bodies=cached_bodies)
    matched = _rank(views, query)
    seen = {_claim_key(view) for view in matched}
    ranked = matched + [view for view in views if _claim_key(view) not in seen]
    provenance = {
        str(row["resource_id"]): [str(seed) for seed in row["linked_from"]] for row in rows
    }
    return [
        view | {"candidate_origin": "linked", "linked_from": provenance[view["id"]]}
        for view in ranked[:limit]
    ]


def _claim_key(view):
    """Parameters: virtual claim. What it does: pairs IDs. Output: stable composite key."""
    return str(view["id"]) + "/" + str(view["entry_id"])


def _rank(views, query):
    """Parameters: claims/query. What it does: BM25-ranks claim text. Output: stable views."""
    ranked = rank_lexical([(view, view["title"] + "\n" + view["body"]) for view in views], query)
    return [view for view, _, _ in sorted(ranked, key=lambda row: (-row[2], _claim_key(row[0])))]


def merge(semantic, lexical, limit):
    """Parameters: vector and lexical ranked virtual claims and one shared result ceiling.
    What it does: fuses composite claim IDs with RRF, keeping only real vector cosine scores.
    Output: deduplicated vector/lexical/hybrid views; no lexical input preserves vector order.
    """
    if limit <= 0:
        return []
    vector_by_id, lexical_by_id = {}, {}
    for view in semantic:
        vector_by_id.setdefault(_claim_key(view), view)
    for view in lexical:
        lexical_by_id.setdefault(_claim_key(view), view)
    order = (
        reciprocal_rank_fusion([list(vector_by_id), list(lexical_by_id)])
        if lexical_by_id
        else vector_by_id
    )
    merged = []
    for key in list(order)[:limit]:
        if key in vector_by_id:
            origin = "hybrid" if key in lexical_by_id else "vector"
            view = dict(vector_by_id[key])
        else:
            origin = "lexical"
            view = dict(lexical_by_id[key])
            view.pop("score", None)
        merged.append(view | {"candidate_origin": origin})
    return merged
