from copy import deepcopy
from types import SimpleNamespace

from test_database_setup import database as database_fixture
from test_item_placement import save
from test_memora_pipeline import Models
from test_memora_workspace import claim_change
from test_memory_classification import response
from test_memory_routing import claims, mapped, route, rules_on
from test_workspace import command_database as command_database_fixture
from test_workspace import commands as commands_fixture

from space import space_map
from space.model_provider import DecisionError

database = database_fixture
command_database = command_database_fixture
commands = commands_fixture


class RoutingModels(Models):
    """Placement wording is a rule; a container matches when the rule names it."""

    settings = SimpleNamespace(memory_classification_provider="decision")

    def __init__(self):
        super().__init__()
        self.fail_candidates = False

    def session(self, authorize):
        session = super().session(authorize)

        def evaluate(payload):
            authorize()
            self.calls.append(("decision", deepcopy(payload)))
            state = payload["state"]
            if "rule" in state:
                if self.fail_candidates:
                    raise DecisionError("Unavailable")
                text = state["rule"]["text"]
                scores = {
                    f"c{n}": 0.95 if c["name"] in text else 0.2
                    for n, c in enumerate(state["containers"])
                }
                return response(payload, scores)
            text = state["memory"]["text"]
            scores = {"global": 0.95 if "every project" in text else 0.1}
            if "routing" in payload["questions"]:
                scores["routing"] = 0.95 if any(w in text for w in ("go in", "never")) else 0.1
            return response(payload, scores)

        session.decide = evaluate
        return session


def requests(service, kind):
    if kind == "candidates":
        return [
            c[1]
            for c in service.memory_models.calls
            if c[0] == "decision" and "rule" in c[1]["state"]
        ]
    return [
        c[1]
        for c in service.memory_models.calls
        if c[0] == "decision" and "memory" in c[1]["state"]
    ]


def setup(commands):
    service, owner, space, _ = commands
    service.memory_models = RoutingModels()
    outreach = save(service, owner, space, "database", name="Clients & Outreach")
    experiments = save(service, owner, space, "database", name="Outreach Experiments")
    return service, owner, space, outreach, experiments


def test_detected_rules_attach_only_to_named_containers(commands):
    service, owner, space, outreach, experiments = setup(commands)
    saved = claims(service, owner, space, ("Contacts go in Clients & Outreach.", None))
    assert saved["memory"]["claims"][0]["routing"] == {
        "target_ids": [outreach["id"]],
        "method": "inferred",
    }
    [rule] = next(
        e for e in space_map.containers(mapped(service, owner, space)) if e["id"] == outreach["id"]
    )["rules"]
    assert rule["origin"] == "inferred"
    assert rules_on(mapped(service, owner, space), "Outreach Experiments") == []
    [candidates] = requests(service, "candidates")
    assert len(candidates["state"]["containers"]) <= 8


def test_facts_cost_no_extra_request_and_explicit_routing_is_never_reasked(commands):
    service, owner, space, outreach, experiments = setup(commands)
    claims(service, owner, space, ("Mira travels by car.", None))
    assert len(requests(service, "classification")) == 1
    assert requests(service, "candidates") == []
    service.memory_models.calls.clear()
    pinned = claims(
        service,
        owner,
        space,
        ("Contacts go in Clients & Outreach.", [experiments["id"]]),
    )
    [classification] = requests(service, "classification")
    assert "routing" not in classification["questions"]
    assert requests(service, "candidates") == []
    assert pinned["memory"]["claims"][0]["routing"]["target_ids"] == [experiments["id"]]


def test_editing_a_rule_into_a_fact_removes_inferred_routing_but_keeps_a_manual_clear(commands):
    service, owner, space, outreach, _ = setup(commands)
    saved = claims(service, owner, space, ("Contacts go in Clients & Outreach.", None))
    claim = saved["memory"]["claims"][0]
    edited = service.change_memory(
        owner, space, claim_change(saved, claim["id"], "edit", value="Contacts are warm leads.")
    )
    assert "routing" not in edited["memory"]["claims"][0]
    assert rules_on(mapped(service, owner, space), "Clients & Outreach") == []
    rule = claims(service, owner, space, ("Leads go in Clients & Outreach.", None))
    cleared = route(service, owner, space, rule, 0, [])
    again = service.change_memory(
        owner,
        space,
        claim_change(
            cleared,
            cleared["memory"]["claims"][0]["id"],
            "edit",
            value="Hot leads go in Clients & Outreach.",
        ),
    )
    assert again["memory"]["claims"][0]["routing"] == {"target_ids": [], "method": "manual"}


def test_global_rules_and_candidate_outages(commands):
    service, owner, space, outreach, _ = setup(commands)
    claims(service, owner, space, ("For every project, never store secrets.", None))
    value = mapped(service, owner, space)
    assert [r["text"] for r in value["global_rules"]] == ["For every project, never store secrets."]
    service.memory_models.fail_candidates = True
    saved = claims(service, owner, space, ("Contacts go in Clients & Outreach.", None))
    assert "routing" not in saved["memory"]["claims"][0]
