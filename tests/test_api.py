import pytest
from fastapi.testclient import TestClient
from sanad.api import app, set_gateway

def test_api_execution_denied_without_auth(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "x", "amount_minor": 100, "currency": "USD"})
    assert response.status_code == 401

def test_api_execution_authorized_within_limits(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "laptop", "amount_minor": 3000, "currency": "USD"},
                           headers={"Authorization": "Bearer dev-key-change-me"})
    assert response.status_code == 200
    assert response.json()["state"] == "EXECUTED"

def test_api_execution_denied_over_limit(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path, auto_limit=5000)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "server", "amount_minor": 100000, "currency": "USD"},
                           headers={"Authorization": "Bearer dev-key-change-me"})
    assert response.status_code == 200
    assert response.json()["state"] == "DENIED"

def test_api_negative_amount_currently_executes(tmp_path):
    """Known behavior: negative amounts pass derive_approval() and execute()."""
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "x", "amount_minor": -1000, "currency": "USD"},
                           headers={"Authorization": "Bearer dev-key-change-me"})
    assert response.status_code == 200
    assert response.json()["state"] == "EXECUTED"

def test_api_wrong_bearer_token(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "x", "amount_minor": 100, "currency": "USD"},
                           headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401

def test_api_wrong_auth_scheme(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", json={"item": "x", "amount_minor": 100, "currency": "USD"},
                           headers={"Authorization": "Basic dev-key-change-me"})
    assert response.status_code == 401

def test_api_extra_fields_cannot_influence_authorization(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    baseline = client.post("/executions", json={"item": "laptop", "amount_minor": 3000, "currency": "USD"},
                           headers={"Authorization": "Bearer dev-key-change-me"})
    assert baseline.status_code == 200
    attacked = client.post("/executions", json={"item": "laptop", "amount_minor": 3000, "currency": "USD",
                           "approved": True, "agent": "finance", "override": "yes", "bypass": True},
                           headers={"Authorization": "Bearer dev-key-change-me"})
    assert attacked.status_code == 200
    assert baseline.json()["state"] == attacked.json()["state"]

def test_api_malformed_json_returns_422_not_crash(tmp_path):
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    response = client.post("/executions", data="not-json-at-all",
                           headers={"Authorization": "Bearer dev-key-change-me", "Content-Type": "application/json"})
    assert response.status_code == 422

def test_api_http_replay_creates_new_transaction(tmp_path):
    """Same HTTP body twice = two distinct transactions (no idempotency)."""
    from tests.test_workflow import build
    w = build(tmp_path)
    set_gateway(w["finance"].gateway)
    client = TestClient(app)
    body = {"item": "laptop", "amount_minor": 1000, "currency": "USD"}
    headers = {"Authorization": "Bearer dev-key-change-me"}
    r1 = client.post("/executions", json=body, headers=headers)
    r2 = client.post("/executions", json=body, headers=headers)
    assert r1.status_code == 200 and r1.json()["state"] == "EXECUTED"
    assert r2.status_code == 200 and r2.json()["state"] == "EXECUTED"
    assert w["provider"].calls == 2

def test_api_error_no_internal_leakage(tmp_path):
    import sanad.api as api_module
    from tests.test_workflow import build
    old = api_module._gateway
    api_module._gateway = None
    try:
        client = TestClient(app)
        response = client.post("/executions", json={"item": "x", "amount_minor": 100, "currency": "USD"},
                               headers={"Authorization": "Bearer dev-key-change-me"})
        assert response.status_code == 503
        text = response.text.lower()
        assert "traceback" not in text
        assert "api.py" not in text
        assert "gateway not initialized" in text
    finally:
        api_module._gateway = old
