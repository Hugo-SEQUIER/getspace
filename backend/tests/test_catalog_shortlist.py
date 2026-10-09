import json
from uuid import uuid4

from space import catalog_shortlist as shortlist


def entry(kind, name, description="", **data):
    return {"id": str(uuid4()), "kind": kind, "name": name, "description": description, **data}


def test_mentions_prefer_longer_names_and_match_whole_words_case_insensitively():
    atlas, mobile = entry("project", "Atlas"), entry("project", "Atlas Mobile")
    dev, atlantic = entry("category", "Dev"), entry("category", "Atlantic")
    items = [atlas, mobile, dev, atlantic]
    assert shortlist.mentions("Ship atlas mobile today", items) == [mobile]
    assert shortlist.mentions("Atlas and ATLAS MOBILE both changed", items) == [mobile, atlas]
    assert shortlist.mentions("Developers love Atlantis", items) == []


def test_shortlist_orders_mentions_then_neighbors_and_adds_project_sections():
    section = entry("section", "Clients")
    named = entry("project", "Predella", section_id=section["id"])
    near = entry("category", "Business")
    far = entry("category", "Food")
    items = [section, named, near, far]
    vectors = {
        section["id"]: [0.0, 1.0],
        named["id"]: [0.0, 1.0],
        near["id"]: [1.0, 0.1],
        far["id"]: [0.0, 1.0],
    }
    result = shortlist.shortlist("Predella pricing", [1.0, 0.0], items, vectors)
    assert [i["id"] for i in result][:3] == [named["id"], section["id"], near["id"]]
    assert len(result) == 4


def test_shortlist_without_a_vector_uses_names_only_and_respects_the_cap():
    items = [entry("category", f"Topic {n}") for n in range(40)]
    named = entry("project", "Atlas")
    assert shortlist.shortlist("Atlas launch", None, [*items, named], {}) == [named]
    vectors = {i["id"]: [1.0, float(n)] for n, i in enumerate(items)}
    result = shortlist.shortlist("Nothing named", [1.0, 0.0], items, vectors)
    assert result == items[: shortlist.SEMANTIC_NEIGHBORS["category"]]
    many = [entry("project", f"P{n}", section_id=None) for n in range(30)]
    text = " ".join(i["name"] for i in many)
    assert len(shortlist.shortlist(text, None, many, {})) == shortlist.MAX_SHORTLIST


def test_trimmed_shortens_only_copies_and_reports_impossible_states():
    items = [entry("project", "Atlas", "x" * 5000), entry("project", "Mobile", "short")]

    def state(copies):
        return {"catalog": [{"name": i["name"], "description": i["description"]} for i in copies]}

    copies = shortlist.trimmed(state, items, 2000)
    assert len(json.dumps(state(copies), ensure_ascii=False)) <= 2000
    assert copies[0]["description"].endswith("…") and copies[1]["description"] == "short"
    assert items[0]["description"] == "x" * 5000
    assert shortlist.trimmed(state, items, 10) is None
    assert shortlist.trimmed(state, [], 100) == []


def test_specific_projects_cannot_crowd_out_broad_categories():
    projects = [entry("project", f"Service {n}") for n in range(30)]
    dev = entry("category", "Dev")
    vectors = {p["id"]: [1.0, 0.01 * n] for n, p in enumerate(projects)}
    vectors[dev["id"]] = [1.0, 2.0]
    result = shortlist.shortlist("Fix the parser", [1.0, 0.0], [*projects, dev], vectors)
    assert dev in result
    assert len([i for i in result if i["kind"] == "project"]) == 10
