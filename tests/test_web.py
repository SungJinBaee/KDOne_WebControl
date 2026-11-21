import json
import time
from unittest.mock import Mock

import pytest

from home_service import HomeError, HomeService
from schedules import Schedules
from webapp import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DEMO": True, "START_SCHEDULER": False, "DATA_DIR": str(tmp_path)})
    yield app
    app.extensions["jobs"].close()


def action(family="ventilation", field="PowerStatus", value="On", group="0", sub="1"):
    return dict(family=family, field=field, value=value, group=group, sub=sub)


def post(client, path, payload):
    csrf = client.get("/api/session").json["csrf"]
    return client.post(path, json=payload, headers={"X-CSRF-Token": csrf})


def test_admin_authentication_and_csrf(tmp_path):
    app = create_app({"TESTING": True, "DEMO": False, "ADMIN_PASSWORD": "long-test-password", "START_SCHEDULER": False, "DATA_DIR": str(tmp_path)})
    client = app.test_client()
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/login", json={"password": "long-test-password"}).status_code == 403
    assert post(client, "/api/login", {"password": "wrong-password"}).status_code == 401
    assert post(client, "/api/login", {"password": "long-test-password"}).status_code == 200
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/control", json={"confirm": True}).status_code == 403
    assert post(client, "/api/logout", {}).status_code == 200
    assert client.get("/api/state").status_code == 401
    app.extensions["jobs"].close()


def test_login_rate_limit(tmp_path):
    app = create_app({"TESTING": True, "DEMO": False, "ADMIN_PASSWORD": "long-test-password", "START_SCHEDULER": False, "DATA_DIR": str(tmp_path)})
    client = app.test_client()
    for _ in range(10):
        assert post(client, "/api/login", {"password": "bad"}).status_code == 401
    assert post(client, "/api/login", {"password": "long-test-password"}).status_code == 429
    app.extensions["jobs"].close()


def test_demo_controls_and_no_remote_network(app, monkeypatch):
    network = Mock(side_effect=AssertionError("Demo must never call real services"))
    monkeypatch.setattr("requests.post", network)
    client = app.test_client()
    assert client.get("/").status_code == 200
    response = client.get("/api/state?refresh=1")
    assert response.status_code == 200
    assert len(response.json["devices"]["heating"]) == 5
    response = post(client, "/api/control", {"action": action(value="Off"), "confirm": True})
    assert response.status_code == 200
    assert response.json["devices"][0]["states"]["PowerStatus"] == "Off"
    assert post(client, "/api/elevator", {"confirm": True}).status_code == 200
    network.assert_not_called()


@pytest.mark.parametrize("field,value", [("GasValveStatus", "Open"), ("InductionStatus", "On"), ("GasDetection", "0")])
def test_no_gas_opening_or_cooktop_energizing(app, field, value):
    client = app.test_client()
    response = post(client, "/api/control", {"action": action("hybrid", field, value), "confirm": True})
    assert response.status_code == 400


@pytest.mark.parametrize("value", ["4", "46", "nan", "Infinity", "21.5", "bad"])
def test_temperature_validation(app, value):
    response = post(app.test_client(), "/api/control", {"action": action("heating", "SettingTemp", value, "1", "2"), "confirm": True})
    assert response.status_code == 400


def test_temperature_room_and_request_confirmation(app):
    home = app.extensions["home"]
    client = app.test_client()
    request = action("heating", "SettingTemp", "21", "1", "2")
    assert post(client, "/api/control", {"action": request}).status_code == 400
    assert post(client, "/api/control", {"action": request, "confirm": True}).status_code == 200
    rooms = home.devices("heating")
    assert rooms[1]["states"]["SettingTemp"] == "21.0"
    assert rooms[0]["states"]["SettingTemp"] == "23.0"


def test_sleep_transition_order_and_failed_intermediate(app, monkeypatch):
    home = app.extensions["home"]
    home.demo_devices["ventilation"][0]["states"]["Mode"] = "Sleep"
    real_send = home._send_control
    calls = []
    def send(value):
        calls.append(value["value"])
        real_send(value)
    monkeypatch.setattr(home, "_send_control", send)
    home.control(action(field="Mode", value="Auto"))
    assert calls == ["Normal", "Auto"]
    home.demo_devices["ventilation"][0]["states"]["Mode"] = "Sleep"
    calls.clear()
    monkeypatch.setattr(home, "_send_control", lambda value: calls.append(value["value"]))
    monkeypatch.setattr("home_service.time.sleep", lambda _: None)
    with pytest.raises(HomeError):
        home.control(action(field="Mode", value="Pleasant"))
    assert calls == ["Normal"]


def test_authentication_request_flow_no_password_in_browser_state(monkeypatch):
    home = HomeService()
    api = Mock(access_token="TOKEN", username="USER", password="PASSWORD")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=api))
    home.request_code("100118", "USER", "PASSWORD")
    api._get_certification_code.assert_called_once()
    home.certify("123456")
    api.certify.assert_called_once_with("123456")
    api.get_token.assert_called_once()
    encoded = json.dumps(home.state())
    for secret in ("TOKEN", "USER", "PASSWORD", "123456"):
        assert secret not in encoded


def test_successful_write_readback_failure_is_warning(app, monkeypatch):
    home = app.extensions["home"]
    real_devices = home.devices
    calls = 0
    def read(family):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise HomeError("readback failed")
        return real_devices(family)
    monkeypatch.setattr(home, "devices", read)
    assert home.control(action())["warning"]


def test_schedule_mode_wind_power_and_auto_off_persist(app, tmp_path):
    home = app.extensions["home"]
    jobs = app.extensions["jobs"]
    now = time.time()
    job_id = jobs.add(action(), now, 1, "취침", [action(field="Mode", value="Sleep"), action(field="WindPower", value="2")])
    jobs.tick(now + 1)
    vent = home.devices("ventilation")[0]["states"]
    assert (vent["Mode"], vent["WindPower"], vent["PowerStatus"]) == ("Sleep", "2", "On")
    # Reopen the same database to verify OFF survives server restart.
    other = Schedules(tmp_path / "demo.sqlite3", home)
    other.tick(now + 61)
    assert home.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert all(row["status"] == "done" for row in other.list())
    assert job_id in [row["id"] for row in other.list()]
    other.close()


def test_schedule_cancel_pair_and_no_past_energizing(app):
    home, jobs = app.extensions["home"], app.extensions["jobs"]
    now = time.time()
    job_id = jobs.add(action(), now, 1)
    jobs.cancel(job_id)
    assert all(row["status"] == "cancelled" for row in jobs.list())
    second = jobs.add(action(), now, 1)
    jobs.tick(now + 65)
    assert next(row for row in jobs.list() if row["id"] == second)["status"] == "skipped"
    assert home.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"


def test_schedule_waits_for_authentication(app, monkeypatch):
    home, jobs = app.extensions["home"], app.extensions["jobs"]
    now = time.time()
    jobs.add(action(), now, 1)
    monkeypatch.setattr(home, "demo", False)
    jobs.tick(now+1)
    assert all(row["status"] == "pending" for row in jobs.list())
    monkeypatch.setattr(home, "demo", True)
    jobs.tick(now+2)
    assert home.devices("ventilation")[0]["states"]["PowerStatus"] == "On"


def test_schedule_rejects_cross_device_setup_and_hybrid(app):
    jobs = app.extensions["jobs"]
    with pytest.raises(HomeError):
        jobs.add(action(), time.time(), 1, setup=[action("heating", "SettingTemp", "21", "1", "2")])
    with pytest.raises(HomeError):
        jobs.add(action("hybrid", "GasValveStatus", "Close"), time.time())


def test_schedule_failures_are_visible_and_not_retried(app, monkeypatch):
    home, jobs = app.extensions["home"], app.extensions["jobs"]
    jobs.add(action(), time.time())
    send = Mock(side_effect=HomeError("server offline"))
    monkeypatch.setattr(home, "control", send)
    jobs.tick(time.time()+1)
    jobs.tick(time.time()+2)
    assert send.call_count == 1
    assert jobs.list()[0]["status"] == "failed"


def test_bad_json_and_xss_text_only(app):
    client = app.test_client()
    assert post(client, "/api/control", []).status_code == 400
    response = client.get("/")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"


def test_schedule_api_setup_and_confirmation(app):
    client = app.test_client()
    payload = {"action": action(), "due": time.time(), "duration": 1,
               "setup": [action(field="Mode", value="Sleep"), action(field="WindPower", value="3")]}
    assert post(client, "/api/schedules", payload).status_code == 400
    response = post(client, "/api/schedules", {**payload, "confirm": True})
    assert response.status_code == 200
    jobs = app.extensions["jobs"]
    jobs.tick(time.time()+1)
    states = app.extensions["home"].devices("ventilation")[0]["states"]
    assert states["Mode"] == "Sleep" and states["WindPower"] == "3"
    jobs.tick(time.time()+65)
    assert app.extensions["home"].devices("ventilation")[0]["states"]["PowerStatus"] == "Off"


def test_multiple_process_start_does_not_replay_running_job(app, tmp_path):
    jobs = app.extensions["jobs"]
    job_id = jobs.add(action(), time.time())
    with jobs.lock:
        jobs.db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
        jobs.db.commit()
    restarted = Schedules(tmp_path / "demo.sqlite3", app.extensions["home"])
    assert restarted.list()[0]["status"] == "failed"
    restarted.close()


def test_immediate_timer_and_missing_time(app):
    client = app.test_client()
    assert post(client, "/api/schedules", {"action": action(), "confirm": True}).status_code == 400
    response = post(client, "/api/schedules", {"action": action(), "confirm": True, "start_now": True, "duration": 1})
    assert response.status_code == 200
    jobs = app.extensions["jobs"]
    jobs.tick(time.time()+1)
    assert any(row["status"] == "done" for row in jobs.list())


def test_admin_password_minimum_eight_characters(tmp_path):
    config = {"TESTING": True, "DEMO": False, "START_SCHEDULER": False,
              "DATA_DIR": str(tmp_path), "CONFIG_DIR": str(tmp_path / "config")}
    with pytest.raises(RuntimeError):
        create_app({**config, "ADMIN_PASSWORD": "1234567"})
    app = create_app({**config, "ADMIN_PASSWORD": "12345678"})
    try:
        assert post(app.test_client(), "/api/login", {"password": "12345678"}).status_code == 200
    finally:
        app.extensions["jobs"].close()


def test_timer_automation_endpoints(app):
    client = app.test_client()
    response = post(client, "/api/timers", {"action": action(), "duration": 1})
    assert response.status_code == 200
    identity = response.json["id"]
    assert post(client, f"/api/timers/{identity}/stop", {}).status_code == 200
    response = post(client, "/api/automations", {"action": action(), "duration": 10,
        "rule": {"kind": "weekdays", "at": "08:00", "days": [0, 2, 4]}})
    assert response.status_code == 200
    identity = response.json["id"]
    assert client.get("/api/schedules").json["automations"][0]["enabled"]
    assert post(client, f"/api/automations/{identity}/toggle", {"enabled": False}).status_code == 200
    assert not client.get("/api/schedules").json["automations"][0]["enabled"]
    assert post(client, f"/api/automations/{identity}/delete", {}).status_code == 200
    assert client.get("/api/schedules").json["automations"] == []


def test_sequence_automation_api(app):
    client = app.test_client()
    response = post(client, "/api/automations", {"label": "Only off", "sequence": [
        {"type": "action", "action": action(value="Off")}],
        "rule": {"kind": "daily", "at": "08:00"}})
    assert response.status_code == 200
    item = client.get("/api/schedules").json["automations"][0]
    assert item["action"]["sequence"][0]["action"]["value"] == "Off"
    assert item["duration"] == 0


def test_edit_sequence_endpoints(app):
    client = app.test_client()
    sequence = [{"type": "wait", "minutes": 1}, {"type": "action", "action": action(value="Off")}]
    response = post(client, "/api/timers", {"sequence": sequence, "label": "Delayed"})
    assert response.status_code == 200
    identity = response.json["id"]
    sequence[0]["minutes"] = 2
    assert post(client, f"/api/timers/{identity}/edit", {"sequence": sequence, "label": "Edited"}).status_code == 200
    item = client.get("/api/schedules").json["timers"][0]
    assert item["sequence"][0]["minutes"] == 2 and item["label"] == "Edited"
    assert post(client, f"/api/timers/{identity}/stop", {}).status_code == 200
    assert client.get("/api/schedules").json["timers"] == []
    rule = {"kind": "daily", "at": "08:00"}
    identity = post(client, "/api/automations", {"sequence": sequence, "rule": rule}).json["id"]
    assert post(client, f"/api/automations/{identity}/edit", {"sequence": [sequence[1]], "rule": rule, "label": "Off only"}).status_code == 200
    assert client.get("/api/schedules").json["automations"][0]["label"] == "Off only"
