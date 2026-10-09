from fastapi.testclient import TestClient

from space.app import create_app
from space.observability import RequestMetrics
from space.settings import Settings


def test_metrics_require_separate_operator_credential_and_hide_readiness_details(monkeypatch):
    import space.app

    token = "synthetic-operator-token-" + "x" * 32
    configuration = Settings(environment="test", allowed_hosts=["testserver"], ops_token=token)
    monkeypatch.setattr(space.app, "database_health", lambda _: {"queued": 2})
    with TestClient(create_app(configuration)) as client:
        assert client.get("/ops/metrics").status_code == 404
        assert (
            client.get("/ops/metrics", headers={"authorization": "Bearer bad"}).status_code == 404
        )
        result = client.get("/ops/metrics", headers={"authorization": f"Bearer {token}"})
        assert result.status_code == 200
        assert result.json()["database"] == {"queued": 2}
        assert token not in result.text
        assert client.get("/health/ready").json() == {"status": "unavailable"}
    metrics = RequestMetrics()
    for status in (200, 404, 503):
        metrics.record(status, 0.05)
    assert metrics.snapshot()["responses"] == {"2xx": 1, "4xx": 1, "5xx": 1}
