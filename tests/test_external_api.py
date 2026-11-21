import json
import time

import pytest

from webapp import create_app


KEY = "test-only-" + "x" * 40
ACTION = dict(family="ventilation", group="0", sub="1", field="PowerStatus", value="Off")


@pytest.fixture
def api(tmp_path):
    directory = tmp_path / "config"
    directory.mkdir()
    secret_file = directory / "secrets.json"
    secret_file.write_text(json.dumps({"api_keys": [{"name":"iPhone", "key":KEY,
        "scopes":["read", "control", "timers", "automations"]}]}), encoding="utf-8")
    app = create_app({"TESTING":True, "DEMO":True, "START_SCHEDULER":False,
                      "CONFIG_DIR":str(directory), "DATA_DIR":str(tmp_path)})
    yield app, app.test_client(), secret_file
    app.extensions["jobs"].close()


def test_key_required_even_in_demo_and_browser_session(api):
    app, client, path = api
    assert client.get("/api/external/state").status_code == 401
    assert client.get("/api/external/state", headers={"X-API-Key":"wrong"}).status_code == 401
    with client.session_transaction() as session:
        session["admin"] = True
    assert client.get("/api/external/state").status_code == 401
    assert client.get("/api/external/state", headers={"X-API-Key":KEY}).status_code == 200


def test_control_without_cookie_csrf_and_no_secret_leak(api):
    app, client, path = api
    headers = {"X-API-Key": KEY}
    response = client.post("/api/external/control", json={"action":ACTION}, headers=headers)
    assert response.status_code == 200
    assert app.extensions["home"].devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert client.post("/api/control", json={"action":ACTION,"confirm":True}, headers=headers).status_code == 403
    assert KEY not in client.get("/api/settings").text
    assert KEY not in client.get("/api/external/state", headers=headers).text
    assert client.post("/api/external/control", json=[], headers=headers).status_code == 400
    assert client.post("/api/external/control", json={"action":{**ACTION,"value":"invalid"}}, headers=headers).status_code == 400


def test_scopes_and_revocation_are_live(api):
    app, client, path = api
    headers = {"X-API-Key": KEY}
    data = json.loads(path.read_text())
    data["api_keys"][0]["scopes"] = ["read"]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/api/external/state", headers=headers).status_code == 200
    assert client.post("/api/external/control", json={"action":ACTION}, headers=headers).status_code == 403
    data["api_keys"][0]["enabled"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/api/external/state", headers=headers).status_code == 401


def test_timer_and_manual_automation_do_not_change_recurrence(api):
    app, client, path = api
    headers = {"X-API-Key":KEY}
    sequence = [{"type":"wait","minutes":1},{"type":"action","action":ACTION}]
    assert client.post("/api/external/timers", json={"sequence":sequence}, headers=headers).status_code == 200
    jobs = app.extensions["jobs"]
    identity = jobs.add_sequence(sequence, {"kind":"daily","at":"08:00"})
    jobs.set_automation(identity, False)
    before = jobs.automation_list()[0]["next_due"]
    endpoint = f"/api/external/automations/{identity}/run"
    assert client.post(endpoint, json={}, headers=headers).status_code == 200
    assert client.post(endpoint, json={}, headers=headers).status_code == 409
    assert jobs.automation_list()[0]["next_due"] == before
    assert not jobs.automation_list()[0]["enabled"]
    assert client.get("/api/external/schedules", headers=headers).status_code == 200
    jobs.tick(time.time()+61)
    assert app.extensions["home"].devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert client.post("/api/external/automations/"+"f"*32+"/run", json={}, headers=headers).status_code == 404


def test_legacy_manual_run_preserves_auto_off(api):
    app, client, path = api
    jobs = app.extensions["jobs"]
    now = time.time()
    identity = jobs.add_automation({**ACTION,"value":"On"}, {"kind":"daily","at":"08:00"}, 1)
    jobs.run_automation(identity)
    jobs.tick(now+1)
    assert app.extensions["home"].devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    jobs.tick(now+62)
    assert app.extensions["home"].devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
