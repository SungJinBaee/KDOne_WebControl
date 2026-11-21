import time

import pytest

from home_service import HomeService, HomeError
from schedules import Schedules


ON = dict(family="ventilation", group="0", sub="1", field="PowerStatus", value="On")
OFF = {**ON, "value": "Off"}


def act(action):
    return {"type": "action", "action": action}


def wait(minutes):
    return {"type": "wait", "minutes": minutes}


@pytest.fixture
def jobs(tmp_path):
    result = Schedules(tmp_path / "edit.sqlite3", HomeService(demo=True))
    yield result
    result.close()


def test_delayed_timer_multiple_actions_persists_and_disappears(jobs, tmp_path):
    now = time.time()
    light = dict(family="lights", group="1", sub="1", field="OnOffStatus", value="Off")
    identity = jobs.save_timer_sequence([wait(1), act(OFF), act(light)], "After one minute")
    jobs.tick(now+59)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    assert jobs.timer_list()[0]["id"] == identity
    other = Schedules(tmp_path / "edit.sqlite3", jobs.service)
    try:
        other.tick(now+61)
        assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
        other.tick(now+62)
        assert jobs.service.devices("lights")[0]["states"]["OnOffStatus"] == "Off"
        assert not other.timer_list()
        assert len(other.list()) == 2
    finally:
        other.close()


def test_edit_timer_cancels_stale_pending_job(jobs):
    now = time.time()
    identity = jobs.save_timer_sequence([wait(1), act(OFF)])
    stale = jobs.db.execute("SELECT * FROM jobs WHERE pair_id=?", (identity,)).fetchone()
    jobs.save_timer_sequence([wait(2), act({**ON, "field": "Mode", "value": "Normal"})], "Changed", identity)
    jobs._execute(stale, now+61)
    jobs.tick(now+61)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    assert jobs.timer_list()[0]["status"] == "active"
    jobs.tick(now+121)
    assert jobs.service.devices("ventilation")[0]["states"]["Mode"] == "Normal"
    assert not jobs.timer_list()


def test_cancel_timer_prevents_delayed_actions(jobs):
    now = time.time()
    identity = jobs.save_timer_sequence([wait(1), act(OFF)])
    jobs.stop_timer(identity)
    jobs.tick(now+61)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    assert not jobs.timer_list()


def test_legacy_timer_can_be_edited(jobs):
    now = time.time()
    identity = jobs.add_timer(ON, 1)
    jobs.tick(now+1)
    jobs.save_timer_sequence([wait(2), act(OFF)], "Edited legacy", identity)
    jobs.tick(now+61)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    jobs.tick(now+121)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"


def test_automation_edit_keeps_running_snapshot_and_paused_flag(jobs, monkeypatch):
    now = time.time()
    rule = {"kind": "interval", "minutes": 10, "anchor": now+2}
    identity = jobs.add_sequence([act(ON), wait(1), act(OFF)], rule, "Before")
    jobs.tick(now+3)
    jobs.set_automation(identity, False)
    jobs.edit_automation(identity, [act(ON)], rule, "After")
    assert not jobs.automation_list()[0]["enabled"]
    jobs.tick(now+64)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    monkeypatch.setattr("schedules.time.time", lambda: now+64)
    jobs.set_automation(identity, True)
    jobs.tick(now+603)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    assert jobs.automation_list()[0]["last_status"] == "done"


def test_past_interval_anchor_is_allowed_on_edit(jobs):
    now = time.time()
    identity = jobs.add_sequence([act(OFF)], {"kind": "daily", "at": "08:00"})
    jobs.edit_automation(identity, [act(OFF)], {"kind": "interval", "minutes": 10, "anchor": now-86400}, "Old anchor")
    assert jobs.automation_list()[0]["next_due"] > now


def test_invalid_edit_keeps_original_schedule(jobs):
    identity = jobs.save_timer_sequence([wait(1), act(OFF)])
    with pytest.raises(HomeError):
        jobs.save_timer_sequence([act(ON)], "Invalid", identity)
    assert jobs.timer_list()[0]["sequence"][0]["minutes"] == 1


def test_edit_not_started_automation_replaces_pending_snapshot(jobs, monkeypatch):
    now = time.time()
    rule = {"kind": "interval", "minutes": 10, "anchor": now+2}
    identity = jobs.add_sequence([wait(1), act(OFF)], rule)
    jobs.tick(now+3)
    monkeypatch.setattr("schedules.time.time", lambda: now+4)
    jobs.edit_automation(identity, [act(OFF)], rule, "Changed")
    jobs.tick(now+64)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    jobs.tick(now+603)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"


def test_immediate_then_wait_timer_and_failed_edit(jobs, monkeypatch):
    now = time.time()
    identity = jobs.save_timer_sequence([act(OFF), wait(1), act(ON)])
    jobs.tick(now+1)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    jobs.tick(now+62)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    assert not jobs.timer_list()
    with pytest.raises(HomeError):
        jobs.save_timer_sequence([wait(1), act(OFF)], "Completed", identity)
