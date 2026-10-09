"""Parameters: authorized selected recall records.
What it does: adds bounded, source-derived identity context without rewriting evidence.
Output: descriptive metadata for assessment and citations, never generated facts.
"""

from space import workspace_items

MAX_LABELS = 64
MAX_LABELS_PER_KIND = 3
MAX_SOURCES = 50
MAX_SOURCES_PER_ITEM = 3
KINDS = {"project": "projects", "category": "categories", "section": "sections"}


def attach(workspace, identity, space_id, items):
    """Parameters: scoped workspace and already selected recall items.

    What it does: resolves only referenced active label names and visible source titles
    at cited revisions. Caps metadata reads and marks omissions; never reads source bodies.
    Output: enriches items in place with evidence_context, preserving authored bodies.
    """
    wanted = {}
    references = {}
    for item in items:
        classification = item.get("classification") or {}
        for kind in KINDS:
            ids = list(classification.get(kind + "_ids", []))
            if kind == "project" and item.get("_context_project"):
                ids.append(str(item["_context_project"]))
            for id_ in ids[:MAX_LABELS_PER_KIND]:
                if len(wanted) < MAX_LABELS:
                    wanted.setdefault(str(id_), kind)
        for ref in item.get("sources", [])[:MAX_SOURCES_PER_ITEM]:
            if len(references) < MAX_SOURCES:
                references.setdefault((str(ref["resource_id"]), ref["revision"]), None)
    labels, source_titles = _metadata(workspace, identity, space_id, wanted, references)
    for item in items:
        context = dict(item.get("evidence_context") or {"resource_title": item["title"]})
        complete = True
        classification = item.get("classification") or {}
        if classification.get("scope"):
            context["scope"] = classification["scope"]
        for kind, field in KINDS.items():
            ids = list(dict.fromkeys(map(str, classification.get(kind + "_ids", []))))
            if kind == "project" and (project := item.pop("_context_project", None)):
                ids = list(dict.fromkeys([*ids, str(project)]))
            names = [
                labels[id_]["name"]
                for id_ in ids[:MAX_LABELS_PER_KIND]
                if id_ in labels and labels[id_]["kind"] == kind
            ]
            if names:
                context[field] = names
            complete &= len(names) == len(ids)
        refs = item.get("sources", [])
        titles = [
            source_titles[(str(ref["resource_id"]), ref["revision"])]
            for ref in refs[:MAX_SOURCES_PER_ITEM]
            if (str(ref["resource_id"]), ref["revision"]) in source_titles
        ]
        if titles:
            context["source_titles"] = titles
        complete &= len(titles) == len(refs)
        if not complete:
            context["incomplete"] = True
        item["evidence_context"] = context
    workspace._recheck(identity, space_id)


def _metadata(workspace, identity, space_id, wanted, references):
    """Parameters: scoped reader, bounded label IDs and exact cited revisions.

    What it does: selects metadata in one authorized transaction, then reads labels
    and current sources before resolving permitted cited titles.
    Output: active label names and visible titles; no source bodies are read.
    """
    if not wanted and not references:
        return {}, {}
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        label_rows, label_keys = _label_rows(workspace, connection, scope, wanted)
        current_rows, current_keys = (
            workspace._item_rows(
                connection, scope, space_id, list(dict.fromkeys(id_ for id_, _ in references))
            )
            if references
            else ([], {})
        )
        cited_rows, cited_keys = _source_rows(workspace, connection, scope, references)
    labels = (
        {
            item["id"]: {"name": item["name"], "kind": item["kind"]}
            for item in workspace_items.decode(
                workspace, identity, space_id, scope, label_rows, label_keys
            )
        }
        if wanted
        else {}
    )
    if not references:
        return labels, {}
    visible = workspace._summaries(
        current_rows, workspace._unwrap_keys(scope, current_keys), scope, include_excluded=False
    )
    visible_ids = {item["id"] for item in visible}
    cited_rows = [row for row in cited_rows if str(row["resource_id"]) in visible_ids]
    if not cited_rows:
        return labels, {}
    # Current metadata must authorize a source before any cited key is unwrapped.
    cited_keys = {id_: cited_keys[id_] for id_ in {row["key_id"] for row in cited_rows}}
    summaries = workspace._summaries(
        cited_rows, workspace._unwrap_keys(scope, cited_keys), scope, include_excluded=False
    )
    return labels, {(item["id"], item["revision"]): item["title"] for item in summaries}


def _label_rows(workspace, connection, scope, wanted):
    """Parameters: authorized transaction, scope and bounded label IDs.

    What it does: selects active label rows and their key rows without reading them.
    Output: raw rows and key rows; foreign, archived and missing labels are absent.
    """
    if not wanted:
        return [], {}
    rows = connection.execute(
        workspace_items.ROW + "WHERE i.id=ANY(%s::uuid[]) "
        "AND i.kind IN ('project','category','section') AND NOT i.deleted "
        "AND v.revision=i.current_revision ORDER BY i.id",
        (list(wanted),),
    ).fetchall()
    return rows, workspace_items.item_keys(workspace, connection, scope, rows)


def _source_rows(workspace, connection, scope, references):
    """Parameters: authorized transaction, scope and bounded cited (ID, revision) pairs.

    What it does: selects exact cited revision metadata and key rows, not bodies.
    Output: raw rows and key rows; current visibility is checked before reading.
    """
    if not references:
        return [], {}
    rows = connection.execute(
        "SELECT r.id AS resource_id,r.kind,r.memory_kind,v.revision,v.key_id,"
        "v.encrypted_metadata,v.object_key,v.created_at,"
        "v.author_client_id,v.author_principal_id "
        "FROM space.resources r JOIN space.resource_revisions v "
        "ON (r.organization_id,r.space_id,r.id)="
        "(v.organization_id,v.space_id,v.resource_id) "
        "WHERE (v.resource_id,v.revision) IN "
        "(SELECT * FROM unnest(%s::uuid[],%s::integer[]))",
        ([id_ for id_, _ in references], [revision for _, revision in references]),
    ).fetchall()
    keys = {id_: workspace._key_row(connection, scope, id_) for id_ in {r["key_id"] for r in rows}}
    return rows, keys
