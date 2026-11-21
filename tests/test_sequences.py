import time

import pytest

from home_service import HomeService, HomeError
from schedules import Schedules


ON = dict(family="ventilation", group="0", sub="1", field="PowerStatus", value="On")
OFF = {**ON, "value": "Off"}


@pytest.fixture
def jobs(tmp_path):
    scheduler = Schedules(tmp_path / "sequence.sqlite3", HomeService(demo=True))
    yield scheduler
    scheduler.close()


def create(jobs, sequence, now):
    return jobs.add_sequence(sequence, {"kind": "interval", "minutes": 10, "anchor": now+2}, "script")


def action(value):
    return {"type": "action", "action": value}


def test_sequence_can_only_stop_or_only_start(jobs):
    now = time.time()
    identity = create(jobs, [action(OFF)], now)
    jobs.tick(now+3)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert jobs.automation_list()[0]["last_status"] == "done"
    assert len(jobs.list()) == 1  # No implicit start/end is appended.
    jobs.set_automation(identity, delete=True)
    create(jobs, [action(ON)], now)
    jobs.tick(now+3)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"


def test_waits_survive_restart_and_allow_other_device(jobs, tmp_path):
    now = time.time()
    light = dict(family="lights", group="1", sub="1", field="OnOffStatus", value="Off")
    create(jobs, [action(OFF), {"type": "wait", "minutes": 1}, action(ON), action(light)], now)
    jobs.tick(now+3)
    jobs.tick(now+62)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    other = Schedules(tmp_path / "sequence.sqlite3", jobs.service)
    try:
        other.tick(now+64)
        assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
        other.tick(now+65)
        assert jobs.service.devices("lights")[0]["states"]["OnOffStatus"] == "Off"
        assert other.automation_list()[0]["last_status"] == "done"
    finally:
        other.close()


def test_pause_keeps_current_sequence_and_failure_stops_later_steps(jobs, monkeypatch):
    now = time.time()
    identity = create(jobs, [action(ON), {"type": "wait", "minutes": 1}, action(OFF)], now)
    jobs.tick(now+3)
    jobs.set_automation(identity, False)
    jobs.tick(now+64)
    assert jobs.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    create(jobs, [action(ON), action(OFF)], now)
    def fail(_):
        raise HomeError("offline")
    monkeypatch.setattr(jobs.service, "control", fail)
    jobs.tick(now+3)
    assert not any(row["status"] == "pending" for row in jobs.list())
    assert any(row["status"] == "failed" for row in jobs.list())


def test_invalid_sequence_is_atomic(jobs):
    now = time.time()
    for sequence in [[], [{"type": "wait", "minutes": 1}],
                     [action(ON), {"type": "wait", "minutes": 10}, action(OFF)],
                     [action({**ON, "value": "Invalid"})]]:
        with pytest.raises(HomeError):
            create(jobs, sequence, now)
    assert not jobs.automation_list()
    assert not jobs.list()


def test_overlapping_sequence_is_not_started_twice(jobs):
    now = time.time()
    create(jobs, [action(ON), {"type": "wait", "minutes": 1}, action(OFF)], now)
    jobs.tick(now+3)
    # Simulate a delayed pending command across the next recurrence.
    jobs.tick(now+603)
    assert len([row for row in jobs.list() if row["action"]["value"] == "On"]) == 1
