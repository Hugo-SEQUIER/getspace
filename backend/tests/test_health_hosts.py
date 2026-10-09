"""Load-balancer liveness must not authorize other hosts or application routes."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from space.health_hosts import HealthCheckedHostMiddleware


def test_private_ip_probe_does_not_broaden_application_host_access():
    app = FastAPI()
    app.add_middleware(HealthCheckedHostMiddleware, allowed_hosts=["api.example.invalid"])

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/v1/workspace")
    def private():
        return {"private": "fixture"}

    with TestClient(app) as client:
        assert client.get("/health/live", headers={"host": "172.31.2.3:8080"}).status_code == 200
        for path in ["/v1/workspace", "/health/ready", "/health/live/", "/ops/metrics"]:
            assert client.get(path, headers={"host": "172.31.2.3:8080"}).status_code == 400
        assert client.post("/health/live", headers={"host": "172.31.2.3"}).status_code == 400
        valid = client.get("/v1/workspace", headers={"host": "api.example.invalid"})
        assert valid.status_code == 200
