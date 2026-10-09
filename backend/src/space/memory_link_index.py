"""Parameters: scoped SQL connections and canonical encrypted-record claims.
What it does: derives opaque navigation edges without storing claim text or confidence.
Output: current outgoing links and backlinks; access and encrypted exclusions remain
    the caller's responsibility. This module never calls a model or commits.
"""

from uuid import UUID

MAX_LINKS_PER_CLAIM = 5
MAX_NODE_LINKS = 20
RELATIONS = {"related", "expands_on"}


def _references(claims: list[dict]) -> list[tuple]:
    """Parameters: canonical claims. What it does: bounds valid edges. Output: tuples."""
    references = []
    for claim in claims:
        relationships = claim.get("relationships")
        if not isinstance(relationships, dict) or relationships.get("status") != "matched":
            continue
        links = relationships.get("links")
        if not isinstance(links, list):
            continue
        try:
            source_id = UUID(str(claim["id"]))
        except (KeyError, ValueError, TypeError):
            continue
        seen = set()
        for link in links[:MAX_LINKS_PER_CLAIM]:
            if not isinstance(link, dict) or link.get("relation") not in RELATIONS:
                continue
            confidence = link.get("confidence")
            revision = link.get("revision")
            if (
                type(confidence) not in (int, float)
                or not 0.9 <= confidence <= 1
                or type(revision) is not int
                or revision < 1
            ):
                continue
            try:
                target = (UUID(str(link["resource_id"])), UUID(str(link["entry_id"])))
            except (KeyError, ValueError, TypeError):
                continue
            hashes = (link.get("source_text_hash"), link.get("target_text_hash"))
            if hashes == (None, None):
                source_hash = target_hash = None
            else:
                try:
                    if any(not isinstance(value, str) or len(value) != 64 for value in hashes):
                        continue
                    source_hash, target_hash = (bytes.fromhex(value) for value in hashes)
                    if len(source_hash) != 32 or len(target_hash) != 32:
                        continue
                except ValueError:
                    continue
            reference = (source_id, *target, revision, source_hash, target_hash, link["relation"])
            if reference not in seen:
                references.append(reference)
                seen.add(reference)
    return references


def _replace_nodes(connection, memory_id, revision: int, links) -> None:
    """Parameters: scoped connection and explicit links. What it does: syncs nodes. Output: none."""
    connection.execute(
        "DELETE FROM space.memory_node_links WHERE source_memory_id=%s", (memory_id,)
    )
    references = []
    for link in list(links)[:MAX_NODE_LINKS]:
        if not isinstance(link, dict) or link.get("relation") not in RELATIONS:
            continue
        try:
            references.append((UUID(str(link["resource_id"])), link["relation"]))
        except (KeyError, ValueError, TypeError):
            continue
    if references:
        connection.cursor().executemany(
            "INSERT INTO space.memory_node_links "
            "(organization_id, space_id, source_memory_id, target_memory_id, relation) "
            "SELECT s.organization_id, s.space_id, s.id, t.id, %s FROM space.resources s "
            "JOIN space.resources t ON t.organization_id=s.organization_id "
            "AND t.space_id=s.space_id WHERE s.id=%s AND s.current_revision=%s "
            "AND t.id=%s AND s.id<>t.id "
            "AND s.memory_kind='memory' AND t.memory_kind='memory' ON CONFLICT DO NOTHING",
            [(relation, memory_id, revision, target) for target, relation in references],
        )


def replace(connection, memory_id, revision: int, claims: list[dict], *, links=()) -> None:
    """Parameters: scoped connection, current revision, claims and explicit node links.
    What it does: synchronizes both indexes after brain.apply in the caller's transaction.
        Malformed, self, foreign and stale references are skipped. Inferred links with
        two hashes follow unchanged text; legacy links require the exact target revision.
        Replacing legacy source edges uses the caller-validated current source revision.
    Output: None. Resource authorization and transaction ownership stay with the caller.
    """
    _replace_nodes(connection, memory_id, revision, links)
    references = _references(claims)
    connection.execute("DELETE FROM space.memory_links WHERE source_memory_id=%s", (memory_id,))
    if not references:
        return
    connection.cursor().executemany(
        "WITH requested AS (SELECT %s::uuid AS source_id, %s::uuid AS target_memory, "
        "%s::uuid AS target_id, %s::integer AS target_revision, "
        "%s::bytea AS source_hash, %s::bytea AS target_hash, %s::text AS relation) "
        "INSERT INTO space.memory_links (organization_id, space_id, source_memory_id, "
        "source_claim_id, source_revision, target_memory_id, target_claim_id, target_revision, "
        "source_text_hash, target_text_hash, relation) "
        "SELECT s.organization_id, s.space_id, s.memory_id, s.claim_id, s.revision, "
        "t.memory_id, t.claim_id, t.revision, r.source_hash, r.target_hash, r.relation "
        "FROM requested r JOIN space.memory_claims s ON s.claim_id=r.source_id "
        "JOIN space.memory_claims t ON t.organization_id=s.organization_id "
        "AND t.space_id=s.space_id AND t.memory_id=r.target_memory AND t.claim_id=r.target_id "
        "JOIN space.resources sr ON sr.organization_id=s.organization_id "
        "AND sr.space_id=s.space_id AND sr.id=s.memory_id AND sr.current_revision=s.revision "
        "JOIN space.resources tr ON tr.organization_id=t.organization_id "
        "AND tr.space_id=t.space_id AND tr.id=t.memory_id AND tr.current_revision=t.revision "
        "WHERE s.memory_id=%s AND s.revision=%s AND s.memory_id<>t.memory_id "
        "AND ((r.source_hash IS NULL AND t.revision=r.target_revision) "
        "OR (s.text_hash=r.source_hash AND t.text_hash=r.target_hash)) "
        "ON CONFLICT DO NOTHING",
        [(*reference, memory_id, revision) for reference in references],
    )


def prune(connection, memory_id) -> None:
    """Parameters: scoped connection, reconciled node. What it does: prunes dead edges.
    Output: none; unchanged hash-pinned edges survive revision and model changes.
    """
    for side in ("source", "target"):
        connection.execute(
            "DELETE FROM space.memory_links l "  # noqa: S608 - side is a closed constant
            f"WHERE l.{side}_memory_id=%s AND NOT EXISTS ("
            "SELECT 1 FROM space.memory_claims c WHERE c.organization_id=l.organization_id "
            f"AND c.space_id=l.space_id AND c.memory_id=l.{side}_memory_id "
            f"AND c.claim_id=l.{side}_claim_id AND "
            f"((l.{side}_text_hash IS NULL AND c.revision=l.{side}_revision) "
            f"OR c.text_hash=l.{side}_text_hash))",
            (memory_id,),
        )


def neighbor_ids(connection, memory_id) -> list[str]:
    """Parameters: scoped connection, focal node. What it does: finds graph candidates.
    Output: sorted opaque focal/adjacent resource IDs from both indexes without a limit.
        No claim text or encrypted metadata is read; stale/excluded nodes may be included.
    """
    rows = connection.execute(
        "SELECT id AS memory_id FROM space.resources WHERE id=%s "
        "UNION SELECT target_memory_id FROM space.memory_links WHERE source_memory_id=%s "
        "UNION SELECT source_memory_id FROM space.memory_links WHERE target_memory_id=%s "
        "UNION SELECT target_memory_id FROM space.memory_node_links WHERE source_memory_id=%s "
        "UNION SELECT source_memory_id FROM space.memory_node_links WHERE target_memory_id=%s "
        "ORDER BY memory_id",
        (memory_id,) * 5,
    ).fetchall()
    return [str(row["memory_id"]) for row in rows]


def read(connection, memory_id, limit: int = 50, *, visible_ids=None, fresh_ids=None) -> dict:
    """Parameters: scoped connection, focal node, limit and optional authorized ID sets.
    What it does: restricts both endpoints to visible IDs, inferred endpoints also to
        fresh IDs, then combines and limits edges. None leaves that filter disabled.
        The caller must validate the encrypted visibility manifest in this transaction.
    Output: {links, backlinks, has_more}; opaque IDs, live other revision, relation, origin.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    visible = None if visible_ids is None else list(visible_ids)
    fresh = None if fresh_ids is None else list(fresh_ids)
    result = {"links": [], "backlinks": [], "has_more": False}
    for key, focal, other in (("links", "source", "target"), ("backlinks", "target", "source")):
        other_claim = "t" if other == "target" else "s"
        other_resource = "tr" if other == "target" else "sr"
        rows = connection.execute(
            f"SELECT l.{focal}_claim_id AS entry_id, "  # noqa: S608
            f"l.{other}_memory_id AS resource_id, "
            f"l.{other}_claim_id AS related_entry_id, {other_claim}.revision AS revision, "
            "l.relation, 'inferred' AS origin FROM space.memory_links l "
            "JOIN space.memory_claims s ON s.organization_id=l.organization_id "
            "AND s.space_id=l.space_id AND s.memory_id=l.source_memory_id "
            "AND s.claim_id=l.source_claim_id "
            "AND ((l.source_text_hash IS NULL AND s.revision=l.source_revision) "
            "OR s.text_hash=l.source_text_hash) "
            "JOIN space.memory_claims t ON t.organization_id=l.organization_id "
            "AND t.space_id=l.space_id AND t.memory_id=l.target_memory_id "
            "AND t.claim_id=l.target_claim_id "
            "AND ((l.target_text_hash IS NULL AND t.revision=l.target_revision) "
            "OR t.text_hash=l.target_text_hash) "
            "JOIN space.resources sr ON sr.organization_id=s.organization_id "
            "AND sr.space_id=s.space_id AND sr.id=s.memory_id AND sr.current_revision=s.revision "
            "JOIN space.resources tr ON tr.organization_id=t.organization_id "
            "AND tr.space_id=t.space_id AND tr.id=t.memory_id AND tr.current_revision=t.revision "
            f"WHERE l.{focal}_memory_id=%s "
            "AND (%s::uuid[] IS NULL OR %s::uuid[] @> ARRAY[sr.id,tr.id]) "
            "AND (%s::uuid[] IS NULL OR %s::uuid[] @> ARRAY[sr.id,tr.id]) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM space.memory_node_links n WHERE n.organization_id=l.organization_id "
            "AND n.space_id=l.space_id AND n.source_memory_id=l.source_memory_id "
            "AND n.target_memory_id=l.target_memory_id) UNION ALL "
            f"SELECT NULL AS entry_id, l.{other}_memory_id AS resource_id, "
            f"NULL AS related_entry_id, {other_resource}.current_revision AS revision, "
            "l.relation, 'explicit' AS origin FROM space.memory_node_links l "
            "JOIN space.resources sr ON sr.organization_id=l.organization_id "
            "AND sr.space_id=l.space_id AND sr.id=l.source_memory_id "
            "JOIN space.resources tr ON tr.organization_id=l.organization_id "
            "AND tr.space_id=l.space_id AND tr.id=l.target_memory_id "
            f"WHERE l.{focal}_memory_id=%s AND sr.memory_kind='memory' "
            "AND tr.memory_kind='memory' "
            "AND (%s::uuid[] IS NULL OR %s::uuid[] @> ARRAY[sr.id,tr.id]) "
            "ORDER BY origin, entry_id, resource_id, related_entry_id, relation LIMIT %s",
            (memory_id, visible, visible, fresh, fresh, memory_id, visible, visible, limit + 1),
        ).fetchall()
        result["has_more"] |= len(rows) > limit
        result[key] = [
            {
                "entry_id": str(row["entry_id"]) if row["entry_id"] is not None else None,
                "resource_id": str(row["resource_id"]),
                "related_entry_id": (
                    str(row["related_entry_id"]) if row["related_entry_id"] is not None else None
                ),
                "revision": row["revision"],
                "relation": row["relation"],
                "origin": row["origin"],
            }
            for row in rows[:limit]
        ]
    return result
