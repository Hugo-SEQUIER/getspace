"""Bounded catalog candidates for one claim, so organization scales past a full catalog.

Readable here: item IDs, revisions and the embedding model name. Item vectors are
encrypted under the Space key and ranked in process; SQL never sees a catalog vector.
"""

import json
import math
import re

MAX_SHORTLIST = 24
# Nearest neighbors per kind, so many specific projects cannot crowd out broad categories.
SEMANTIC_NEIGHBORS = {"category": 6, "section": 3, "project": 10, "folder": 4, "database": 4}
PURPOSE = "workspace_item_vector"


def item_text(item):
    return f"{item['kind']}: {item['name']}\n{item.get('description', '')}"


def mentions(text, items):
    """Items named in the text, longest name first, so a longer name hides its prefix."""
    remaining, found = text, []
    for item in sorted(items, key=lambda i: (-len(i["name"]), i["id"])):
        pattern = re.compile(r"(?<!\w)" + re.escape(item["name"]) + r"(?!\w)", re.IGNORECASE)
        if pattern.search(remaining):
            found.append(item)
            remaining = pattern.sub(lambda m: " " * len(m.group(0)), remaining)
    return found


def cosine(left, right):
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def shortlist(text, vector, items, vectors, quotas=SEMANTIC_NEIGHBORS):
    """Mentions, then nearest neighbors per kind, each followed by its project's section."""
    by_id = {item["id"]: item for item in items}
    named = mentions(text, items)
    seen = {item["id"] for item in named}
    ranked = []
    if vector is not None:
        scored = sorted(
            (
                (-cosine(vector, vectors[item["id"]]), item["id"])
                for item in items
                if item["id"] not in seen and item["id"] in vectors
            ),
        )
        taken = dict.fromkeys(quotas, 0)
        for _, id_ in scored:
            kind = by_id[id_]["kind"]
            if taken.get(kind, 0) < quotas.get(kind, 0):
                taken[kind] += 1
                ranked.append(by_id[id_])
    selected, chosen = [], set()
    for item in named + ranked:
        parent = by_id.get(item.get("section_id")) if item["kind"] == "project" else None
        for candidate in (item, parent):
            if candidate is not None and candidate["id"] not in chosen:
                selected.append(candidate)
                chosen.add(candidate["id"])
    return selected[:MAX_SHORTLIST]


def trimmed(state_for, items, limit):
    """Shorten only the provider copy of descriptions until the serialized state fits."""

    def size(copies):
        return len(json.dumps(state_for(copies), ensure_ascii=False))

    longest = max((len(item.get("description", "")) for item in items), default=0)
    width = longest
    while True:
        copies = [
            item
            if len(item.get("description", "")) <= width
            else item | {"description": item["description"][: max(width - 1, 0)] + "…"}
            for item in items
        ]
        if size(copies) <= limit:
            return copies
        if width == 0:
            return None
        width = width * 4 // 5 if width > 20 else 0


def item_vectors(workspace, identity, space_id, items, model):
    """Decrypt cached vectors for current item revisions; embed and cache the rest."""
    embedding_model = workspace.memory_models.embedding_model
    scope, key = workspace._key(identity, space_id, write=True)
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True)
        rows = connection.execute(
            "SELECT item_id,item_revision,key_id,encrypted_vector "
            "FROM space.workspace_item_vectors "
            "WHERE item_id=ANY(%s::uuid[]) AND embedding_model=%s",
            ([item["id"] for item in items], embedding_model),
        ).fetchall()
    revisions = {item["id"]: item["revision"] for item in items}
    vectors = {
        str(row["item_id"]): workspace._unpack(
            key, scope, row["item_id"], row["item_revision"], PURPOSE, row["encrypted_vector"]
        )
        for row in rows
        if str(row["key_id"]) == str(key.key.key_id)
        and revisions.get(str(row["item_id"])) == row["item_revision"]
    }
    missing = [item for item in items if item["id"] not in vectors]
    if not missing:
        return vectors
    # Embedding runs outside every transaction, like claim embedding.
    fresh = model.embed([item_text(item) for item in missing])
    with workspace.db.transaction(identity) as connection:
        workspace._scope(connection, identity, space_id, write=True)
        for item, vector in zip(missing, fresh, strict=True):
            vector = [float(value) for value in vector]
            vectors[item["id"]] = vector
            connection.execute(
                "INSERT INTO space.workspace_item_vectors(organization_id,space_id,item_id,"
                "item_revision,embedding_model,key_id,encrypted_vector) "
                "SELECT %s,%s,%s,%s,%s,%s,%s WHERE EXISTS(SELECT 1 FROM "
                "space.workspace_item_revisions WHERE item_id=%s AND revision=%s) "
                "ON CONFLICT (organization_id,space_id,item_id,embedding_model) DO UPDATE SET "
                "item_revision=excluded.item_revision,key_id=excluded.key_id,"
                "encrypted_vector=excluded.encrypted_vector,created_at=now() "
                "WHERE workspace_item_vectors.item_revision<=excluded.item_revision",
                (
                    scope["organization_id"],
                    scope["space_id"],
                    item["id"],
                    item["revision"],
                    embedding_model,
                    key.key.key_id,
                    workspace._pack(key, scope, item["id"], item["revision"], PURPOSE, vector),
                    item["id"],
                    item["revision"],
                ),
            )
    return vectors
