"""One bounded read that tells an agent where things belong in a Space.

The tree is built from current, non-archived workspace items on every read, so it
cannot drift. Routing rules come from memory. Nothing here calls a model.
"""

import json
from copy import deepcopy

from pydantic import Field

from space import memory_routing, tasks, workspace_items
from space.memory import Input

MAX_ITEMS = 2000
DEFAULT_CHARS = 12_000


class MapRequest(Input):
    max_chars: int = Field(default=DEFAULT_CHARS, ge=4000, le=20_000)


def ordered(items):
    return sorted(items, key=lambda item: (item["name"].casefold(), item["id"]))


def current_items(workspace, identity, space_id):
    """Every active item in one read; databases keep schema and a count, never records."""
    with workspace.db.transaction(identity) as connection:
        scope = workspace._scope(connection, identity, space_id)
        rows = connection.execute(
            workspace_items.ROW
            + "WHERE NOT i.deleted AND v.revision=i.current_revision ORDER BY i.id LIMIT %s",
            (MAX_ITEMS + 1,),
        ).fetchall()
        key_rows = workspace_items.item_keys(workspace, connection, scope, rows[:MAX_ITEMS])
    if not rows:
        return [], False
    items = workspace_items.decode(workspace, identity, space_id, scope, rows[:MAX_ITEMS], key_rows)
    return [workspace_items.without_records(item) for item in items], len(rows) > MAX_ITEMS


def container(item, rules):
    entry = {"id": item["id"], "name": item["name"], "description": item.get("description", "")}
    if item["kind"] == "project":
        entry["status"] = item["status"]
    if item["kind"] == "database":
        entry |= {
            "records": item["record_count"],
            "fields": [{"name": f["name"], "type": f["type"]} for f in item["fields"]],
            "view": item.get("view", "table"),
            "group_by": item.get("groupBy"),
        }
        if tracking := item.get("tasks"):
            # Field names, not IDs, so an agent can query the rows directly.
            names = {f["id"]: f["name"] for f in item["fields"]}
            entry["tasks"] = {
                "status": names.get(tracking["status"]),
                "done": tracking["done"],
                "planned": names.get(tracking["planned"]),
            }
    entry["rules"] = rules.get(item["id"], [])
    return entry


def tree(items, rules):
    by_kind = {kind: [] for kind in ("section", "project", "folder", "database", "category")}
    for item in items:
        by_kind[item["kind"]].append(item)
    active = {item["id"]: item for item in items}
    sections = {
        s["id"]: container(s, rules) | {"projects": [], "folders": [], "databases": []}
        for s in ordered(by_kind["section"])
    }
    unsectioned = {"projects": [], "folders": [], "databases": []}
    projects = {}
    for project in ordered(by_kind["project"]):
        entry = container(project, rules) | {"folders": [], "databases": []}
        projects[project["id"]] = entry
        parent = project.get("section_id")
        if parent in sections:
            sections[parent]["projects"].append(entry)
        else:
            if parent:
                entry["archived_parent"] = True
            unsectioned["projects"].append(entry)
    for kind, plural in (("folder", "folders"), ("database", "databases")):
        for item in ordered(by_kind[kind]):
            entry = container(item, rules)
            project, section = item.get("project_id"), item.get("section_id")
            if project in projects:
                projects[project][plural].append(entry)
            elif not project and section in sections:
                sections[section][plural].append(entry)
            else:
                if (project and project not in active) or (section and section not in active):
                    entry["archived_parent"] = True
                unsectioned[plural].append(entry)
    return {
        "sections": list(sections.values()),
        "unsectioned": unsectioned,
        "categories": [
            {"id": c["id"], "name": c["name"], "description": c.get("description", "")}
            for c in ordered(by_kind["category"])
        ],
    }


def containers(value):
    for section in value["sections"]:
        yield section
        for project in section["projects"]:
            yield project
            yield from project["folders"]
            yield from project["databases"]
        yield from section["folders"]
        yield from section["databases"]
    for project in value["unsectioned"]["projects"]:
        yield project
        yield from project["folders"]
        yield from project["databases"]
    yield from value["unsectioned"]["folders"]
    yield from value["unsectioned"]["databases"]


def _drop_fields(value):
    for entry in containers(value):
        entry.pop("fields", None)


def _drop_weak_rules(value):
    for entry in [*containers(value), {"rules": value["global_rules"]}]:
        entry["rules"][:] = [
            rule for rule in entry["rules"] if rule["origin"] == "explicit" and not rule["stale"]
        ]


def _cut(width):
    def cut(value):
        for entry in [*containers(value), *value["categories"]]:
            text = entry.get("description", "")
            if len(text) > width:
                entry["description"] = text[: width - 1] + "…"

    return cut


def _few_rules(value):
    for entry in containers(value):
        del entry["rules"][2:]
    del value["global_rules"][5:]


def _drop_categories(value):
    value["categories"] = []


def _drop_empty_sections(value):
    value["sections"] = [
        s
        for s in value["sections"]
        if s["projects"] or s["folders"] or s["databases"] or s["rules"]
    ]


REDUCTIONS = (
    _drop_fields,
    _drop_weak_rules,
    _cut(300),
    _cut(120),
    _few_rules,
    _drop_categories,
    _drop_empty_sections,
)


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def bounded(value, max_chars):
    """Reduce in a fixed order; IDs and names are never removed from listed containers."""
    value = deepcopy(value)
    for reduce in REDUCTIONS:
        if size(value) <= max_chars:
            break
        reduce(value)
        value["truncated"] = True
    return value


def build(workspace, identity, space_id, request: MapRequest):
    items, more = current_items(workspace, identity, space_id)
    info = workspace.info(identity, space_id)
    rules, global_rules = memory_routing.rules(
        workspace,
        identity,
        space_id,
        [i["id"] for i in items if i["kind"] in memory_routing.CONTAINERS],
    )
    value = {
        "space": {"id": info["id"], "name": info["name"]},
        "global_rules": global_rules,
        **tree(items, rules),
        "task_statuses": tasks.columns(workspace, identity, space_id)["columns"],
        "truncated": more,
    }
    return bounded(value, request.max_chars)
