"""Integration tests for the REST API and the Vapi tool webhook.

Run with:  pytest -q
Uses a throwaway SQLite file so the tests never touch the Supabase database.
"""
import json
import os
import tempfile

import pytest

os.environ["DATABASE_URL"] = (
    f"sqlite:///{os.path.join(tempfile.gettempdir(), 'test_patients.db')}"
)

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

VALID = {
    "first_name": "Jane", "last_name": "Doe", "date_of_birth": "03/05/1990",
    "sex": "Female", "phone_number": "7132101234",
    "address_line_1": "12 Oak Street", "city": "Houston",
    "state": "TX", "zip_code": "77002",
}


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


def tool_call(client, name, args, caller="+17132101234"):
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"customer": {"number": caller}} if caller else {},
            "toolCallList": [{
                "id": "call_test", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }],
        }
    }
    resp = client.post("/vapi/tools", json=body)
    assert resp.status_code == 200
    return json.loads(resp.json()["results"][0]["result"])


# --- REST API -------------------------------------------------------------

def test_health(client):
    assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None}


def test_create_returns_201_and_envelope(client):
    resp = client.post("/patients", json=VALID)
    assert resp.status_code == 201
    body = resp.json()
    assert body["error"] is None
    assert body["data"]["patient_id"]
    assert body["data"]["preferred_language"] == "English"


def test_create_normalises_spoken_input(client):
    resp = client.post("/patients", json={
        **VALID, "date_of_birth": "March 5th 1990", "sex": "f",
        "state": "Texas", "zip_code": "770021234",
        "phone_number": "(713) 210-1234", "email": "jane at gmail dot com",
    })
    data = resp.json()["data"]
    assert data["date_of_birth"] == "1990-03-05"
    assert (data["sex"], data["state"]) == ("Female", "TX")
    assert data["zip_code"] == "77002-1234"
    assert data["phone_number"] == "7132101234"
    assert data["email"] == "jane@gmail.com"


def test_future_dob_rejected_with_field_detail(client):
    resp = client.post("/patients", json={**VALID, "date_of_birth": "01/01/2035"})
    assert resp.status_code == 422
    assert "future" in resp.json()["error"]["fields"]["date_of_birth"].lower()


def test_short_phone_rejected(client):
    resp = client.post("/patients", json={**VALID, "phone_number": "555"})
    assert resp.status_code == 422
    assert "date_of_birth" not in resp.json()["error"]["fields"]


def test_get_unknown_id_returns_404_envelope(client):
    resp = client.get("/patients/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["data"] is None
    assert resp.json()["error"]["message"]


def test_query_filters(client):
    client.post("/patients", json=VALID)
    assert len(client.get("/patients", params={"last_name": "doe"}).json()["data"]) == 1
    # Filters normalise their input the same way writes do.
    assert len(client.get(
        "/patients", params={"phone_number": "(713) 210-1234"}).json()["data"]) == 1
    assert len(client.get(
        "/patients", params={"date_of_birth": "03/05/1990"}).json()["data"]) == 1
    assert len(client.get(
        "/patients", params={"last_name": "nobody"}).json()["data"]) == 0


def test_partial_update_leaves_other_fields_alone(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    resp = client.put(f"/patients/{pid}", json={"last_name": "Davis"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_name"] == "Davis"
    assert data["first_name"] == "Jane"
    assert data["city"] == "Houston"


def test_empty_update_rejected(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    assert client.put(f"/patients/{pid}", json={}).status_code == 400


def test_delete_is_soft(client):
    pid = client.post("/patients", json=VALID).json()["data"]["patient_id"]
    resp = client.delete(f"/patients/{pid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["deleted_at"] is not None
    # Hidden from normal reads, still present in the table.
    assert client.get(f"/patients/{pid}").status_code == 404
    assert len(client.get("/patients").json()["data"]) == 0
    assert len(client.get(
        "/patients", params={"include_deleted": "true"}).json()["data"]) == 1
    # Deleting twice reports 404 rather than silently succeeding.
    assert client.delete(f"/patients/{pid}").status_code == 404


# --- Vapi tool webhook ----------------------------------------------------

def test_lookup_reports_no_record(client):
    assert tool_call(client, "lookup_patient", {})["found"] is False


def test_register_then_lookup_finds_returning_caller(client):
    result = tool_call(client, "register_patient", {
        k: v for k, v in VALID.items() if k != "phone_number"})
    assert result["success"] is True

    found = tool_call(client, "lookup_patient", {})
    assert found["found"] is True
    assert found["patient"]["last_name"] == "Doe"


def test_register_falls_back_to_caller_id(client):
    """The agent may never ask for a number; caller ID should fill it in."""
    tool_call(client, "register_patient",
              {k: v for k, v in VALID.items() if k != "phone_number"})
    stored = client.get("/patients").json()["data"][0]
    assert stored["phone_number"] == "7132101234"


def test_register_returns_field_specific_errors(client):
    result = tool_call(client, "register_patient", {
        **VALID, "date_of_birth": "01/01/2035", "phone_number": "555"},
        caller=None)
    assert result["success"] is False
    errors = result["validation_errors"]
    assert set(errors) == {"date_of_birth", "phone_number"}
    assert "future" in errors["date_of_birth"].lower()


def test_missing_required_fields_are_named(client):
    result = tool_call(client, "register_patient", {"first_name": "Ann"})
    assert result["success"] is False
    assert "last_name" in result["validation_errors"]
    # phone_number came from caller ID, so it must NOT be reported missing.
    assert "phone_number" not in result["validation_errors"]


def test_update_patient_by_caller_id(client):
    tool_call(client, "register_patient",
              {k: v for k, v in VALID.items() if k != "phone_number"})
    result = tool_call(client, "update_patient", {"last_name": "Davis"})
    assert result["success"] is True
    assert client.get("/patients").json()["data"][0]["last_name"] == "Davis"


def test_unknown_tool_does_not_500(client):
    body = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "x", "function": {"name": "no_such_tool", "arguments": "{}"}}]}}
    assert client.post("/vapi/tools", json=body).status_code == 200


def test_end_of_call_report_is_acknowledged(client):
    resp = client.post("/vapi/tools", json={"message": {
        "type": "end-of-call-report", "endedReason": "customer-ended-call",
        "transcript": "AI: Hello\nUser: Hi"}})
    assert resp.status_code == 200
    assert resp.json() == {"results": []}


def test_legacy_function_call_payload_supported(client):
    resp = client.post("/vapi/tools", json={"message": {
        "type": "function-call",
        "call": {"customer": {"number": "+17132101234"}},
        "functionCall": {"name": "lookup_patient", "parameters": {}}}})
    assert resp.status_code == 200
    assert json.loads(resp.json()["results"][0]["result"])["found"] is False
