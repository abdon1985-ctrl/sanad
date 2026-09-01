import pytest
from fastapi.testclient import TestClient
from sanad.api import app, set_gateway

def test_api_execution_denied_without_auth(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)

    # Send valid body so FastAPI validates it, then auth fails
    response = client.post(
        "/executions",
        json={"item": "x", "amount_minor": 100, "currency": "USD"}
    )
    assert response.status_code == 401

def test_api_execution_authorized_within_limits(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)

    response = client.post(
        "/executions",
        json={"item": "laptop", "amount_minor": 3000, "currency": "USD"},
        headers={"Authorization": "Bearer dev-key-change-me"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "EXECUTED"

def test_api_execution_denied_over_limit(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path, auto_limit=5000)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)

    response = client.post(
        "/executions",
        json={"item": "server", "amount_minor": 100000, "currency": "USD"},
        headers={"Authorization": "Bearer dev-key-change-me"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "DENIED"
