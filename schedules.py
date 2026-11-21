"""Persistent timers and recurring automations, executed by one process."""
import json
import sqlite3
import threading
import time
import uuid
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from home_service import HomeError

KST = timezone(timedelta(hours=9))


def normalize_rule(rule, allow_past=False):
    if not isinstance(rule, dict):
        raise HomeError("반복 주기를 선택하세요.")
    kind = rule.get("kind")
    if kind == "interval":
        minutes, anchor = rule.get("minutes"), rule.get("anchor")
        if type(minutes) is not int or not 1 <= minutes <= 10080:
            raise HomeError("반복 간격은 1~10080분이어야 합니다.")
        if type(anchor) not in (int, float) or not math.isfinite(anchor) or not (0 if allow_past else time.time()-60) <= anchor <= time.time()+366*86400:
            raise HomeError("첫 실행 시각은 현재부터 1년 이내로 설정하세요.")
        return {"kind": kind, "minutes": minutes, "anchor": anchor}
    if kind not in ("daily", "weekdays"):
        raise HomeError("매일·요일별·시간 간격 중 선택하세요.")
    at = rule.get("at", "")
    try:
        parsed = datetime.strptime(at, "%H:%M")
    except (ValueError, TypeError):
        raise HomeError("시작 시각을 HH:MM 형식으로 입력하세요.") from None
    days = list(range(7)) if kind == "daily" else rule.get("days")
    if not isinstance(days, list) or not days or any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise HomeError("반복할 요일을 선택하세요.")
    return {"kind": kind, "at": parsed.strftime("%H:%M"), "days": sorted(set(days))}


def next_occurrence(rule, after):
    """Strictly after `after`, calculated in Korea time; no catch-up bursts."""
    if rule["kind"] == "interval":
        anchor, seconds = rule["anchor"], rule["minutes"] * 60
        index = max(0, math.floor((after-anchor)/seconds)+1)
        return anchor + index * seconds
    local = datetime.fromtimestamp(after, KST)
    hour, minute = map(int, rule["at"].split(":"))
    for offset in range(8):
        candidate = (local + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in rule["days"] and candidate.timestamp() > after:
            return candidate.timestamp()
    raise HomeError("다음 실행 시각을 계산하지 못했습니다.")


class Schedules:
    def __init__(self, path, service):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.service = service
        self.stop_event = threading.Event()
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, due REAL NOT NULL,
            action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            error TEXT, pair_id TEXT, expires REAL)""")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        for name, declaration in (("kind", "TEXT NOT NULL DEFAULT 'legacy'"), ("automation_id", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
        self.db.execute("""CREATE TABLE IF NOT EXISTS automations (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, action TEXT NOT NULL,
            rule TEXT NOT NULL, duration INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            next_due REAL NOT NULL, last_run REAL, last_status TEXT, last_error TEXT)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS timers (
            id TEXT PRIMARY KEY, label TEXT NOT NULL, sequence TEXT NOT NULL,
            status TEXT NOT NULL, updated REAL NOT NULL, error TEXT)""")
        # Do not repeat a possibly completed write after a process crash.
        self.db.execute("UPDATE jobs SET status='failed', error='실행 도중 서버가 재시작되었습니다. 기기 상태를 확인하세요.' WHERE status='running'")
        self.db.execute("UPDATE timers SET status='failed',error='실행 도중 서버가 재시작되었습니다. 기기 상태를 확인하세요.' WHERE status='active' AND id IN (SELECT pair_id FROM jobs WHERE kind='timer_sequence' AND status='failed') AND id NOT IN (SELECT pair_id FROM jobs WHERE status='pending')")
        self.db.commit()

    def list(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM jobs WHERE status IN ('pending','running') OR id IN (SELECT id FROM jobs ORDER BY due DESC LIMIT 200) OR id IN (SELECT pair_id FROM jobs WHERE status IN ('pending','running')) ORDER BY due DESC").fetchall()
            return [{**dict(row), "action": json.loads(row["action"])} for row in rows]

    def _prepare(self, action, setup):
        with self.service.lock:
            clean, device = self.service.validate(action)
            if setup is None:
                setup = []
            if not isinstance(setup, list) or len(setup) > 3:
                raise HomeError("예약 설정 형식이 다릅니다.")
            steps = []
            for item in setup:
                step, _ = self.service.validate(item)
                if (step["family"], step["group"], step["sub"]) != (clean["family"], clean["group"], clean["sub"]):
                    raise HomeError("예약 설정은 같은 기기만 지정할 수 있습니다.")
                if step["field"] not in ("Mode", "WindPower", "SettingTemp"):
                    raise HomeError("예약 사전 설정 항목이 올바르지 않습니다.")
                steps.append(step)
        return clean, device, steps

    def add(self, action, due, duration=0, label="예약", setup=None, kind="legacy"):
        clean, device, steps = self._prepare(action, setup)
        now = time.time()
        if due is None:
            due = now  # Immediate timers begin after live validation completes.
        if not isinstance(due, (int, float)) or not now - 5 <= due <= now + 366 * 86400:
            raise HomeError("예약 시각은 현재부터 1년 이내로 설정해 주세요.")
        if type(duration) is not int or not 0 <= duration <= 7 * 24 * 60:
            raise HomeError("동작 시간은 최대 7일이며 분 단위 정수로 입력해 주세요.")
        if clean["family"] == "hybrid":
            raise HomeError("가스·쿡탑 차단은 예약하지 않고 직접 실행해 주세요.")
        off_fields = {"lights": "OnOffStatus", "ventilation": "PowerStatus", "heating": "Heating"}
        if duration and clean["field"] == off_fields[clean["family"]] and clean["value"] == "Off":
            raise HomeError("끄기 요청에는 동작 시간을 지정할 수 없습니다.")
        group_id = uuid.uuid4().hex
        end = due + duration * 60 if duration else None
        is_off = clean["field"] == off_fields[clean["family"]] and clean["value"] == "Off"
        if is_off and steps:
            raise HomeError("끄기 예약에는 모드·온도 사전 설정을 지정할 수 없습니다.")
        if kind == "timer" and (is_off or not duration):
            raise HomeError("타이머는 켜기 동작과 1분 이상의 종료 시간이 필요합니다.")
        stored = {**clean, "setup": steps}
        expires = None if is_off else end if end else due + 300
        label = f"{device['name']} · {str(label)[:60]}"
        with self.lock:
            self.db.execute("INSERT INTO jobs(id,label,due,action,pair_id,expires,kind) VALUES(?,?,?,?,?,?,?)",
                            (group_id, label, due, json.dumps(stored), group_id, expires, kind))
            if duration:
                off = {**clean, "field": off_fields[clean["family"]], "value": "Off"}
                self.db.execute("INSERT INTO jobs(id,label,due,action,pair_id,kind) VALUES(?,?,?,?,?,?)",
                                (uuid.uuid4().hex, label + " · 자동 끄기", end, json.dumps(off), group_id, kind))
            self.db.commit()
        return group_id

    def add_timer(self, action, duration, setup=None):
        if not isinstance(action, dict) or action.get("field") != {"lights":"OnOffStatus", "ventilation":"PowerStatus", "heating":"Heating"}.get(action.get("family")) or action.get("value") != "On":
            raise HomeError("타이머는 조명·환기·난방의 켜기 동작으로 시작합니다.")
        return self.add(action, None, duration, "타이머", setup, kind="timer")

    def automation_list(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM automations ORDER BY next_due").fetchall()
            return [{**dict(row), "action": json.loads(row["action"]), "rule": json.loads(row["rule"]),
                     "enabled": bool(row["enabled"])} for row in rows]

    def add_automation(self, action, rule, duration, setup=None, label="자동화"):
        rule = normalize_rule(rule)
        period = rule["minutes"] if rule["kind"] == "interval" else 1440
        if type(duration) is not int or not 1 <= duration < period:
            raise HomeError("동작 시간은 1분 이상이며 반복 간격(매일·요일별은 24시간)보다 짧아야 합니다.")
        clean, device, steps = self._prepare(action, setup)
        fields = {"lights":"OnOffStatus", "ventilation":"PowerStatus", "heating":"Heating"}
        if clean["field"] != fields.get(clean["family"]) or clean["value"] != "On":
            raise HomeError("자동화는 조명·환기·난방의 켜기 동작으로 시작합니다.")
        automation_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO automations(id,label,action,rule,duration,next_due) VALUES(?,?,?,?,?,?)",
                (automation_id, device["name"] + " / " + str(label)[:60], json.dumps({**clean, "setup": steps}), json.dumps(rule),
                 duration, next_occurrence(rule, time.time())))
        return automation_id

    def _clean_sequence(self, sequence):
        if not isinstance(sequence, list) or not 1 <= len(sequence) <= 50:
            raise HomeError("동작·대기 행을 1~50개 입력하세요.")
        if not isinstance(sequence[-1], dict) or sequence[-1].get("type") != "action":
            raise HomeError("마지막 행은 기기 동작으로 설정하세요.")
        cleaned, total = [], 0
        with self.service.lock:
            for step in sequence:
                if not isinstance(step, dict):
                    raise HomeError("자동화 행 형식을 확인하세요.")
                if step.get("type") == "wait":
                    minutes = step.get("minutes")
                    if type(minutes) is not int or not 1 <= minutes <= 10080:
                        raise HomeError("대기는 1~10080분 정수로 입력하세요.")
                    total += minutes
                    cleaned.append({"type": "wait", "minutes": minutes})
                elif step.get("type") == "action":
                    raw = step.get("action")
                    action, device, setup = self._prepare(raw, step.get("setup", raw.get("setup") if isinstance(raw, dict) else None))
                    if action["family"] == "hybrid":
                        raise HomeError("가스·쿡탑 차단은 직접 실행하세요.")
                    cleaned.append({"type": "action", "action": {**action, "setup": setup}, "name": device["name"]})
                else:
                    raise HomeError("지원하지 않는 자동화 행입니다.")
        return cleaned, total

    def add_sequence(self, sequence, rule, label="자동화"):
        rule = normalize_rule(rule)
        cleaned, total = self._clean_sequence(sequence)
        period = rule["minutes"] if rule["kind"] == "interval" else 1440
        if total >= period or not any(step["type"] == "action" for step in cleaned):
            raise HomeError("기기 동작이 필요하며 전체 대기는 반복 간격보다 짧아야 합니다.")
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO automations(id,label,action,rule,duration,next_due) VALUES(?,?,?,?,?,?)",
                (identity, str(label or "자동화")[:80], json.dumps({"sequence": cleaned}), json.dumps(rule),
                 total, next_occurrence(rule, time.time())))
        return identity

    def run_automation(self, identity):
        with self.service.lock, self.lock, self.db:
            if not self.service.connected:
                raise HomeError("기기 연결이 필요합니다.", 409)
            row = self.db.execute("SELECT * FROM automations WHERE id=?", (identity,)).fetchone()
            if not row:
                raise HomeError("자동화를 찾지 못했습니다.", 404)
            if self.db.execute("SELECT 1 FROM jobs WHERE automation_id=? AND status IN ('pending','running')", (identity,)).fetchone():
                raise HomeError("이미 실행 중인 자동화입니다.", 409)
            stored = json.loads(row["action"])
            sequence = stored.get("sequence")
            if sequence is None:
                off = {**stored, "setup": [], "field": {"lights":"OnOffStatus", "ventilation":"PowerStatus", "heating":"Heating"}[stored["family"]], "value":"Off"}
                sequence = [{"type":"action", "action":stored, "name":row["label"]},
                            {"type":"wait", "minutes":row["duration"]}, {"type":"action", "action":off, "name":row["label"]}]
            now = time.time()
            self._queue_sequence(sequence, 0, now, None, identity, row["label"])
            self.db.execute("UPDATE automations SET last_run=?,last_status='pending',last_error=NULL WHERE id=?", (now, identity))
        return identity

    def timer_list(self):
        with self.lock:
            rows = self.db.execute("SELECT * FROM timers WHERE status IN ('active','failed') ORDER BY updated DESC").fetchall()
            result = []
            for row in rows:
                pending = self.db.execute("SELECT due FROM jobs WHERE pair_id=? AND status='pending' ORDER BY due LIMIT 1", (row["id"],)).fetchone()
                result.append({**dict(row), "sequence": json.loads(row["sequence"]), "next_due": pending["due"] if pending else None})
            return result

    def save_timer_sequence(self, sequence, label="타이머", timer_id=None):
        cleaned, total = self._clean_sequence(sequence)
        if not 1 <= total <= 10080:
            raise HomeError("타이머의 대기 합계는 1~10080분이어야 합니다.")
        with self.service.lock, self.lock, self.db:
            if timer_id:
                current = self.db.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone()
                legacy = self.db.execute("SELECT id FROM jobs WHERE id=? AND kind='timer' AND id=pair_id", (timer_id,)).fetchone()
                pending = self.db.execute("SELECT 1 FROM jobs WHERE pair_id=? AND status IN ('pending','failed')", (timer_id,)).fetchone()
                if (not current and (not legacy or not pending)) or (current and current["status"] not in ("active", "failed")):
                    raise HomeError("수정 가능한 타이머가 없습니다.", 409)
                self.db.execute("UPDATE jobs SET status='cancelled' WHERE pair_id=? AND status='pending'", (timer_id,))
            timer_id = timer_id or uuid.uuid4().hex
            label = str(label or "타이머")[:80]
            now = time.time()
            self.db.execute("INSERT INTO timers(id,label,sequence,status,updated) VALUES(?,?,?,'active',?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,sequence=excluded.sequence,status='active',updated=excluded.updated,error=NULL",
                            (timer_id, label, json.dumps(cleaned), now))
            self._queue_sequence(cleaned, 0, now, timer_id, None, label, kind="timer_sequence")
        return timer_id

    def edit_automation(self, identity, sequence, rule, label):
        cleaned, total = self._clean_sequence(sequence)
        rule = normalize_rule(rule, allow_past=True)
        if total >= (rule["minutes"] if rule["kind"] == "interval" else 1440):
            raise HomeError("전체 대기는 반복 간격보다 짧아야 합니다.")
        with self.service.lock, self.lock, self.db:
            row = self.db.execute("SELECT * FROM automations WHERE id=?", (identity,)).fetchone()
            if not row:
                raise HomeError("자동화를 찾지 못했습니다.", 404)
            # A running cycle keeps its original snapshot. Cancel only unstarted cycles.
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE automation_id=? AND status='pending' AND (id=pair_id OR pair_id NOT IN (SELECT pair_id FROM jobs WHERE automation_id=? AND status IN ('done','running','failed'))) ", (identity, identity))
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE automation_id=? AND status='pending' AND pair_id IN (SELECT id FROM jobs WHERE status='cancelled')", (identity,))
            self.db.execute("UPDATE automations SET label=?,action=?,rule=?,duration=?,next_due=? WHERE id=?",
                (str(label or "자동화")[:80], json.dumps({"sequence": cleaned}), json.dumps(rule), total,
                 next_occurrence(rule, time.time()), identity))

    def _queue_sequence(self, sequence, index, due, pair, automation_id, label, kind="sequence"):
        while index < len(sequence) and sequence[index]["type"] == "wait":
            due += sequence[index]["minutes"] * 60
            index += 1
        if index == len(sequence):
            return False
        action = {**sequence[index]["action"], "_sequence": sequence, "_index": index, "_label": label}
        identity = uuid.uuid4().hex
        pair = pair or identity
        is_off = action["field"] in ("OnOffStatus", "PowerStatus", "Heating") and action["value"] == "Off"
        self.db.execute("INSERT INTO jobs(id,label,due,action,pair_id,expires,kind,automation_id) VALUES(?,?,?,?,?,?,?,?)",
            (identity, f"{label} · {index+1}행 · {sequence[index]['name']}", due, json.dumps(action), pair,
             None if is_off else due+300, kind, automation_id))
        return True

    def set_automation(self, automation_id, enabled=None, delete=False):
        if not delete and type(enabled) is not bool:
            raise HomeError("자동화 활성화 여부를 지정하세요.")
        with self.lock:
            row = self.db.execute("SELECT * FROM automations WHERE id=?", (automation_id,)).fetchone()
            if not row:
                raise HomeError("자동화를 찾지 못했습니다.", 404)
            if delete:
                self.db.execute("DELETE FROM automations WHERE id=?", (automation_id,))
            else:
                due = next_occurrence(json.loads(row["rule"]), time.time()) if enabled else row["next_due"]
                self.db.execute("UPDATE automations SET enabled=?,next_due=? WHERE id=?", (int(enabled), due, automation_id))
            if delete or not enabled:
                self.db.execute("UPDATE jobs SET status='cancelled' WHERE automation_id=? AND id=pair_id AND status='pending'", (automation_id,))
                self.db.execute("UPDATE jobs SET status='cancelled' WHERE automation_id=? AND status='pending' AND pair_id IN (SELECT id FROM jobs WHERE status='cancelled')", (automation_id,))
            self.db.commit()

    def _materialize(self, now):
        with self.lock:
            rows = self.db.execute("SELECT * FROM automations WHERE enabled=1 AND next_due<=?", (now,)).fetchall()
            with self.db:
                for row in rows:
                    stored = json.loads(row["action"])
                    if "sequence" in stored:
                        following = next_occurrence(json.loads(row["rule"]), now)
                        active = self.db.execute("SELECT 1 FROM jobs WHERE automation_id=? AND status IN ('pending','running')", (row["id"],)).fetchone()
                        self.db.execute("UPDATE automations SET next_due=? WHERE id=?", (following, row["id"]))
                        if active:
                            continue  # Never overlap two runs of the same script.
                        if now-row["next_due"] > 300:
                            self.db.execute("UPDATE automations SET last_run=?,last_status='skipped' WHERE id=?", (row["next_due"], row["id"]))
                            continue
                        self._queue_sequence(stored["sequence"], 0, now, None, row["id"], row["label"])
                        self.db.execute("UPDATE automations SET last_run=?,last_status='pending',last_error=NULL WHERE id=?", (now, row["id"]))
                        continue
                    due, end = row["next_due"], row["next_due"] + row["duration"] * 60
                    following = next_occurrence(json.loads(row["rule"]), now)
                    status = "skipped" if end <= now else "pending"
                    self.db.execute("UPDATE automations SET next_due=?,last_run=?,last_status=?,last_error=NULL WHERE id=?",
                                    (following, due, status, row["id"]))
                    if end <= now:
                        continue
                    action = json.loads(row["action"])
                    off = {**action, "field": {"lights":"OnOffStatus", "ventilation":"PowerStatus", "heating":"Heating"}[action["family"]], "value":"Off", "setup": []}
                    pair = uuid.uuid4().hex
                    self.db.execute("INSERT INTO jobs(id,label,due,action,pair_id,expires,kind,automation_id) VALUES(?,?,?,?,?,?,?,?)",
                        (pair, row["label"], due, row["action"], pair, end, "automation", row["id"]))
                    self.db.execute("INSERT INTO jobs(id,label,due,action,pair_id,kind,automation_id) VALUES(?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, row["label"]+" · 종료", end, json.dumps(off), pair, "automation", row["id"]))

    def stop_timer(self, timer_id):
        with self.service.lock, self.lock:
            timer = self.db.execute("SELECT * FROM timers WHERE id=?", (timer_id,)).fetchone()
            if timer:
                if timer["status"] not in ("active", "failed"):
                    raise HomeError("이미 종료된 타이머입니다.", 409)
                with self.db:
                    self.db.execute("UPDATE jobs SET status='cancelled' WHERE pair_id=? AND status='pending'", (timer_id,))
                    self.db.execute("UPDATE timers SET status='cancelled' WHERE id=?", (timer_id,))
                return
            start = self.db.execute("SELECT * FROM jobs WHERE id=? AND kind='timer' AND id=pair_id", (timer_id,)).fetchone()
            if not start:
                raise HomeError("타이머를 찾지 못했습니다.", 404)
            off = self.db.execute("SELECT * FROM jobs WHERE pair_id=? AND id<>pair_id", (timer_id,)).fetchone()
            if not off or off["status"] not in ("pending", "failed"):
                raise HomeError("이미 종료된 타이머입니다.", 409)
            self.db.execute("UPDATE jobs SET status='cancelled' WHERE pair_id=? AND status='pending'", (timer_id,))
            self.db.commit()
            if start["status"] == "pending":
                return
            try:
                self.service.control(json.loads(off["action"]))
            except HomeError as error:
                self.db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(error), off["id"]))
                self.db.commit()
                raise
            self.db.execute("UPDATE jobs SET status='done',error=NULL WHERE id=?", (off["id"],))
            self.db.commit()

    def cancel(self, job_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise HomeError("예약을 찾지 못했습니다.", 404)
            if row["status"] != "pending":
                raise HomeError("대기 중인 예약만 취소할 수 있습니다.", 409)
            # Cancelling the automatic OFF must not cancel an already executed start.
            if row["id"] == row["pair_id"]:
                self.db.execute("UPDATE jobs SET status='cancelled' WHERE pair_id=? AND status='pending'", (row["pair_id"],))
            else:
                self.db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
            self.db.commit()

    def tick(self, now=None):
        now = time.time() if now is None else now
        self._materialize(now)
        if not self.service.connected:
            return  # Due jobs remain pending until re-authentication.
        with self.lock:
            rows = self.db.execute("SELECT * FROM jobs WHERE status='pending' AND due<=? ORDER BY due", (now,)).fetchall()
        for row in rows:
            self._execute(row, now)

    def _execute(self, row, now):
        with self.service.lock:
            with self.lock:
                current = self.db.execute("SELECT status FROM jobs WHERE id=?", (row["id"],)).fetchone()
                if not current or current["status"] != "pending":
                    return
                if row["expires"] is not None and row["expires"] <= now:
                    self.db.execute("UPDATE jobs SET status='skipped',error='동작 시간이 지나 시작하지 않았습니다.' WHERE id=? AND status='pending'", (row["id"],))
                    if row["automation_id"]:
                        self.db.execute("UPDATE automations SET last_status='skipped',last_error='실행 시각이 지나 동작을 생략했습니다.' WHERE id=?", (row["automation_id"],))
                    if row["kind"] == "timer_sequence":
                        self.db.execute("UPDATE timers SET status='failed',error='실행 시각이 지나 동작을 생략했습니다.' WHERE id=?", (row["pair_id"],))
                    self.db.commit()
                    return
                changed = self.db.execute("UPDATE jobs SET status='running' WHERE id=? AND status='pending'", (row["id"],)).rowcount
                self.db.commit()
            if not changed:
                return
            try:
                action = json.loads(row["action"])
                with self.service.lock:
                    steps = action.get("setup", [])
                    if action["family"] == "ventilation" and action["field"] == "PowerStatus" and action["value"] == "On":
                        # Auto owns fan speed; also handle schedules saved by older versions.
                        mode = next((step["value"] for step in steps if step["field"] == "Mode"), None)
                        if mode is None:
                            devices = self.service.devices("ventilation")
                            target = next((device for device in devices if (device["group"], device["sub"]) ==
                                           (action["group"], action["sub"])), None)
                            mode = target["states"].get("Mode") if target else None
                        if mode == "Auto":
                            steps = [step for step in steps if step["field"] != "WindPower"]
                        # Power/mode transitions can reset or ignore an earlier fan setting.
                        ordered = [action] + sorted(steps, key=lambda step: step["field"] == "WindPower")
                        for step in ordered:
                            self.service.control(step)
                            self._wait_ventilation(step)
                    else:
                        for step in steps:
                            self.service.control(step)
                        self.service.control(action)
                status, error = "done", None
            except HomeError as exc:
                status, error = "failed", str(exc)
            except Exception:
                status, error = "failed", "예약 실행 중 오류가 발생했습니다. 기기 상태를 확인하세요."
            with self.lock:
                self.db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status, error, row["id"]))
                more = False
                if status == "done" and "_sequence" in action:
                    more = self._queue_sequence(action["_sequence"], action["_index"]+1,
                        max(now, time.time()), row["pair_id"], row["automation_id"], action.get("_label", row["label"].split(" · ")[0]), kind=row["kind"])
                if row["kind"] == "timer_sequence":
                    self.db.execute("UPDATE timers SET status=?,error=? WHERE id=?", ("active" if more else status, error, row["pair_id"]))
                if row["automation_id"]:
                    if more:
                        status = "pending"
                    self.db.execute("UPDATE automations SET last_status=?,last_error=? WHERE id=?", (status, error, row["automation_id"]))
                self.db.commit()

    def _wait_ventilation(self, action):
        for attempt in range(5):
            if attempt:
                time.sleep(1)
            devices = self.service.devices("ventilation")
            target = next((device for device in devices if (device["group"], device["sub"]) ==
                           (action["group"], action["sub"])), None)
            if target and str(target["states"].get(action["field"])) == str(action["value"]):
                return
        raise HomeError(f"환기 {action['field']}={action['value']} 적용이 확인되지 않았습니다. 실제 상태를 확인하세요.", 409)

    def start(self):
        def run():
            while not self.stop_event.wait(2):
                self.tick()
        self.thread = threading.Thread(target=run, name="home-schedules", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if hasattr(self, "thread"):
            self.thread.join(timeout=45)
        with self.lock:
            self.db.close()
