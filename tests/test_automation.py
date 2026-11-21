import time
from datetime import datetime

import pytest

from home_service import HomeService, HomeError
from schedules import Schedules, KST, next_occurrence


@pytest.fixture
def scheduler(tmp_path):
    jobs = Schedules(tmp_path / "jobs.sqlite3", HomeService(demo=True))
    yield jobs
    jobs.close()


ACTION = dict(family="ventilation", group="0", sub="1", field="PowerStatus", value="On")


def test_timer_start_stop_and_validation(scheduler):
    with pytest.raises(HomeError):
        scheduler.add_timer(ACTION, 0)
    identity = scheduler.add_timer(ACTION, 2)
    scheduler.tick(time.time() + 1)
    assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    scheduler.stop_timer(identity)
    assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert not any(row["status"] == "pending" for row in scheduler.list())


def test_repeat_and_pause_keeps_current_off(scheduler):
    now = time.time()
    identity = scheduler.add_automation(ACTION, dict(kind="interval", minutes=3, anchor=now+2), 1)
    scheduler.tick(now+3)
    assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
    scheduler.set_automation(identity, False)
    scheduler.tick(now+63)
    assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    scheduler.tick(now+183)
    assert len(scheduler.list()) == 2


def test_repeat_restart_and_missed_windows(scheduler, tmp_path):
    now = time.time()
    scheduler.add_automation(ACTION, dict(kind="interval", minutes=3, anchor=now+2), 1)
    other = Schedules(tmp_path / "jobs.sqlite3", scheduler.service)
    try:
        other.tick(now+70)
        assert other.automation_list()[0]["last_status"] == "skipped"
        assert not other.list()
        other.tick(now+183)
        assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "On"
        other.tick(now+243)
        assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    finally:
        other.close()


def test_korean_weekdays_and_midnight():
    friday = datetime(2026, 11, 20, 23, 59, tzinfo=KST).timestamp()
    next_time = next_occurrence(dict(kind="weekdays", at="08:00", days=[0]), friday)
    assert datetime.fromtimestamp(next_time, KST) == datetime(2026, 11, 23, 8, tzinfo=KST)
    midnight = next_occurrence(dict(kind="daily", at="00:00", days=list(range(7))), friday)
    assert datetime.fromtimestamp(midnight, KST).day == 21


def test_invalid_automation_does_not_leave_temporary_jobs(scheduler):
    for rule, duration in [(dict(kind="daily", at="08:00"), 1440),
                           (dict(kind="weekdays", at="08:00", days=[]), 10),
                           (dict(kind="interval", minutes=10, anchor=time.time()+30), 10)]:
        with pytest.raises(HomeError):
            scheduler.add_automation(ACTION, rule, duration)
    assert scheduler.list() == []
    assert scheduler.automation_list() == []


def test_delete_keeps_running_cycle_end_and_repeat_runs_twice(scheduler):
    now = time.time()
    identity = scheduler.add_automation(ACTION, dict(kind="interval", minutes=3, anchor=now+2), 1)
    scheduler.tick(now+3)
    scheduler.tick(now+63)
    scheduler.tick(now+183)
    assert len(scheduler.list()) == 4
    scheduler.set_automation(identity, delete=True)
    scheduler.tick(now+243)
    assert scheduler.service.devices("ventilation")[0]["states"]["PowerStatus"] == "Off"
    assert scheduler.automation_list() == []


def test_stop_pending_timer_prevents_start(scheduler):
    identity = scheduler.add_timer(ACTION, 2)
    scheduler.stop_timer(identity)
    scheduler.tick(time.time()+1)
    assert all(row["status"] == "cancelled" for row in scheduler.list())


def test_ventilation_fan_applied_after_power_and_mode(scheduler, monkeypatch):
    original = scheduler.service.control
    calls = []

    def resetting_control(action):
        calls.append(action["field"])
        result = original(action)
        if action["field"] in ("PowerStatus", "Mode"):
            original({**ACTION, "field": "WindPower", "value": "1"})
        return result

    monkeypatch.setattr(scheduler.service, "control", resetting_control)
    scheduler.add_timer(ACTION, 1, [{**ACTION, "field": "WindPower", "value": "3"},
                                   {**ACTION, "field": "Mode", "value": "Pleasant"}])
    scheduler.tick(time.time()+1)
    assert calls == ["PowerStatus", "Mode", "WindPower"]
    assert scheduler.service.devices("ventilation")[0]["states"]["WindPower"] == "3"


def test_auto_ignores_saved_manual_fan_setting(scheduler, monkeypatch):
    calls = []
    original = scheduler.service.control
    def record(action):
        calls.append(action["field"])
        return original(action)
    monkeypatch.setattr(scheduler.service, "control", record)
    scheduler.add_timer(ACTION, 1, [{**ACTION, "field": "Mode", "value": "Auto"},
                                   {**ACTION, "field": "WindPower", "value": "2"}])
    scheduler.tick(time.time()+1)
    assert calls == ["PowerStatus", "Mode"]
    assert scheduler.service.devices("ventilation")[0]["states"]["Mode"] == "Auto"
