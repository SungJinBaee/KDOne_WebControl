"""One household connection with external configuration and optional saved auth."""
import contextlib
import io
import json
import threading
import time
from decimal import Decimal, InvalidOperation
from config_store import ConfigError

import requests
import truststore

truststore.inject_into_ssl()
from KDOne.api import KDOneAPI
from KDOne.utils.encryption import encrypt_payload, decrypt_payload, random_key

TYPES = {"lights": "1401", "ventilation": "140B", "heating": "140C", "hybrid": "1402"}
MODES = {"Normal": "일반", "Sleep": "취침", "Pleasant": "전열", "Auto": "자동"}


class HomeError(Exception):
    def __init__(self, message, status=400, remote_rejected=False, auth_rejected=False):
        super().__init__(message)
        self.status = status
        self.remote_rejected = remote_rejected
        self.auth_rejected = auth_rejected


class HomeAPI(KDOneAPI):
    """Use explicit timeouts; never log encryption keys or remote bodies."""
    def _send_request(self, endpoint, data):
        key = random_key()
        with contextlib.redirect_stdout(io.StringIO()):
            body = encrypt_payload(key, json.dumps(data))
        response = requests.post(self.base_url + endpoint, data=body, headers={
            "Content-Type": "text/plain", "User-Agent": "homenet/10 CFNetwork/1568.200.51 Darwin/24.1.0",
            "x-message-id": key}, timeout=(10, 30))
        payload = None
        try:
            response_key = response.headers.get("x-message-id")
            if response_key:
                with contextlib.redirect_stdout(io.StringIO()):
                    payload = json.loads(decrypt_payload(response_key, response.text))
            else:
                payload = response.json()
        except Exception:
            raise HomeError(f"서버 응답을 해석하지 못했습니다 (HTTP {response.status_code}).", 502) from None
        if not isinstance(payload, dict):
            raise HomeError("서버 응답 형식이 다릅니다.", 502)
        code = str(payload.get("Error_Cd", ""))
        if response.status_code != 200 or code != "0000":
            if code == "5001":
                message = "아이디 또는 비밀번호를 확인해 주세요."
            else:
                safe_code = code if code.isascii() and code.isdigit() and len(code) <= 10 else "확인 불가"
                message = f"KDOne 요청 실패: HTTP {response.status_code}, 코드 {safe_code}"
            raise HomeError(message, 502, remote_rejected=True,
                            auth_rejected=response.status_code in (401, 403))
        return payload


def group_rows(rows):
    devices = {}
    for row in rows:
        if not isinstance(row, dict):
            raise HomeError("기기 응답 형식이 다릅니다.", 502)
        group, sub = str(row.get("Group_Id", "")), str(row.get("Sub_Id", ""))
        device = devices.setdefault((group, sub), {
            "group": group, "sub": sub, "name": str(row.get("Device_Remark", "기기")), "states": {}})
        # Return device fields only; never forward a remote response envelope.
        field, value = row.get("Status_Type"), row.get("Status")
        if isinstance(field, str) and isinstance(value, (str, int, float, bool, type(None))):
            device["states"][field] = value
    return list(devices.values())


class HomeService:
    def __init__(self, demo=False, config_store=None):
        self.demo = demo
        self.config_store = config_store
        self.connection_message = ""
        self.storage_warning = ""
        self.auto_connect = True
        self.last_auth_refresh = None
        self.last_auth_attempt = 0
        self.auth_warning = ""
        self.maintenance_stop = threading.Event()
        self.lock = threading.RLock()
        self.api = None
        self.pending = None
        self.complex_name = "호반써밋동탄"
        if config_store:
            self.complex_name = config_store.settings()["complex_name"]
        self.cache = {}
        self.updated = None
        if demo:
            self.demo_devices = self._demo_devices()

    @property
    def connected(self):
        return self.demo or bool(self.api and self.api.access_token)

    def _network(self, function):
        try:
            return function()
        except requests.exceptions.SSLError:
            raise HomeError("TLS 인증서 검증에 실패했습니다. NAS 시간과 인증서를 확인하세요.", 502) from None
        except requests.Timeout:
            raise HomeError("서버 응답 시간이 초과됐습니다. 실제 기기 상태를 조회해 주세요.", 504) from None
        except requests.RequestException:
            raise HomeError("KDOne 서버에 연결하지 못했습니다.", 502) from None

    def complexes(self, keyword=""):
        if self.demo:
            return [{"id": "100118", "name": self.complex_name, "address": "데모 단지"}]
        def load():
            response = requests.post(KDOneAPI.BASE_URL + "/info/complex-list", timeout=(10, 30))
            response.raise_for_status()
            try:
                data = response.json()
                if data.get("Error_Cd") != "0000":
                    raise HomeError("단지 목록 요청이 거부됐습니다.", 502)
                return [{"id": str(item["Complex"]), "name": str(item["Complex_Name"]),
                         "address": str(item.get("Complex_Addr", ""))} for item in data["Resources"]]
            except (ValueError, KeyError, TypeError):
                raise HomeError("단지 목록 응답 형식이 다릅니다.", 502) from None
        with self.lock:
            items = self._network(load)
        keyword = keyword.casefold()
        return [item for item in items if keyword in (item["name"] + item["address"]).casefold()]

    def request_code(self, complex_id, username, password, complex_name=""):
        with self.lock:
            if self.connected and not self.demo:
                raise HomeError("이미 기기 서버에 연결되어 있습니다.")
            if not all(isinstance(v, str) and v for v in (complex_id, username, password)):
                raise HomeError("단지, 아이디, 비밀번호를 입력해 주세요.")
            if self.demo:
                return
            candidate = HomeAPI(username, password, verbose=False)
            candidate.complex_id = complex_id
            self.pending = None
            self._network(candidate._get_certification_code)
            self.pending = (candidate, time.monotonic() + 600, complex_name)

    def certify(self, code):
        with self.lock:
            if self.demo:
                return
            if not self.pending or self.pending[1] < time.monotonic():
                self.pending = None
                raise HomeError("인증 요청이 만료됐습니다. 인증번호를 다시 요청하세요.")
            candidate, _, name = self.pending
            self._network(lambda: candidate.certify(code))
            self._network(candidate.get_token)
            if not candidate.access_token:
                raise HomeError("토큰이 발급되지 않았습니다.", 502)
            self.api, self.pending = candidate, None
            self.complex_name = name or "우리 집"
            self.connection_message = ""
            self.auto_connect = True
            self.last_auth_refresh = time.time()
            self.last_auth_attempt = time.monotonic()
            self.auth_warning = ""
            self.cache.clear()
            self.storage_warning = ""
            if self.config_store:
                try:
                    self.config_store.save_account({"complex_id": candidate.complex_id,
                        "complex_name": self.complex_name, "username": candidate.username,
                        "password": candidate.password, "app_certify": candidate.app_certify})
                except (ConfigError, OSError):
                    self.storage_warning = "연결은 완료됐으나 외부 인증 파일 저장에 실패했습니다. config 폴더 쓰기 권한을 확인하세요."

    def request_saved_code(self):
        if not self.config_store:
            raise HomeError("외부 설정 폴더가 없습니다.")
        account = self.config_store.secrets().get("account", {})
        self.request_code(account.get("complex_id"), account.get("username"),
                          account.get("password"), account.get("complex_name", ""))

    def restore_connection(self, force=False):
        with self.lock:
            if self.demo or not self.config_store or (self.connected and not force):
                return self.connected
            self.last_auth_attempt = time.monotonic()
            account = self.config_store.secrets().get("account", {})
            if not all(account.get(key) for key in ("complex_id", "username", "password", "app_certify")):
                self.connection_message = "저장된 인증정보가 없습니다. 월패드 인증으로 연결하세요."
                return False
            candidate = HomeAPI(account["username"], account["password"], verbose=False)
            candidate.complex_id = account["complex_id"]
            candidate.app_certify = account["app_certify"]
            try:
                self._network(candidate.get_token)
                if not candidate.access_token:
                    raise HomeError("토큰이 발급되지 않았습니다.")
            except HomeError as error:
                self.auth_warning = f"자동 인증 갱신 실패: {error} 연결 설정에서 인증 상태를 확인하세요."
                if not self.connected:
                    self.connection_message = self.auth_warning
                return False
            self.api, self.pending = candidate, None
            self.complex_name = account.get("complex_name") or "우리 집"
            self.connection_message = ""
            self.auth_warning = ""
            self.last_auth_refresh = time.time()
            self.cache.clear()
            return True

    def maintain_connection(self):
        with self.lock:
            if self.pending and self.pending[1] < time.monotonic():
                self.pending = None
            if self.demo or not self.auto_connect or self.pending or not self.config_store:
                return
            minutes = self.config_store.settings()["session_refresh_minutes"]
            delay = 60 if self.auth_warning or not self.connected else minutes * 60
            if time.monotonic() - self.last_auth_attempt >= delay:
                self.restore_connection(force=True)

    def start_maintenance(self):
        def run():
            while not self.maintenance_stop.wait(15):
                try:
                    self.maintain_connection()
                except (ConfigError, OSError):
                    self.auth_warning = "외부 설정을 읽지 못해 자동 갱신을 중단했습니다. 설정 파일을 확인하세요."
                except Exception:
                    self.auth_warning = "자동 갱신 처리 중 오류가 발생했습니다. 연결 설정을 확인하세요."
        self.maintenance_thread = threading.Thread(target=run, name="home-auth", daemon=True)
        self.maintenance_thread.start()

    def stop_maintenance(self):
        self.maintenance_stop.set()
        if hasattr(self, "maintenance_thread"):
            self.maintenance_thread.join(timeout=45)

    def _authenticated_request(self, endpoint, fields, read_only=False):
        def send():
            return self._network(lambda: self.api._send_request(endpoint, {
                **fields, "Complex": self.api.complex_id,
                "Access_Token": self.api.access_token, "Refresh_Token": self.api.refresh_token}))
        try:
            return send()
        except HomeError as error:
            # A read may be repeated after any explicit remote rejection. Writes
            # are repeated only for an explicit HTTP 401/403, never timeouts.
            if (error.auth_rejected or (read_only and error.remote_rejected)):
                if self.restore_connection(force=True):
                    return send()
                if error.auth_rejected:
                    self.api = None
            raise

    def _apply_names(self, family, devices):
        names = self.config_store.settings()["device_names"] if self.config_store else {}
        result = []
        for device in devices:
            source = device.get("source_name", device["name"])
            result.append({**device, "source_name": source,
                           "name": names.get(f"{family}/{device['group']}/{device['sub']}", source)})
        return result

    def _rows(self, family):
        if family not in TYPES:
            raise HomeError("지원하지 않는 기기입니다.")
        if not self.connected:
            if not self.auto_connect or not self.restore_connection():
                raise HomeError("자동 연결에 실패했습니다. 연결 설정에서 인증 상태를 확인하세요.", 409)
        if self.demo:
            rows = []
            for device in self.demo_devices[family]:
                for field, value in device["states"].items():
                    rows.append({"Device_Type": TYPES[family], "Group_Id": device["group"],
                                 "Sub_Id": device["sub"], "Device_Remark": device["name"],
                                 "Status_Type": field, "Status": value})
            return rows
        data = self._authenticated_request("/device/list", {"Device_Type": TYPES[family]}, read_only=True)
        rows = data.get("Resources")
        if not isinstance(rows, list):
            raise HomeError("기기 목록 응답 형식이 다릅니다.", 502)
        return rows

    def devices(self, family):
        with self.lock:
            result = self._apply_names(family, group_rows(self._rows(family)))
            self.cache[family] = result
            self.updated = time.time()
            return result

    def state(self, refresh=False):
        with self.lock:
            errors = {}
            if refresh and self.connected:
                for family in TYPES:
                    try:
                        self.devices(family)
                    except HomeError as error:
                        errors[family] = str(error)
            for family, devices in self.cache.items():
                self.cache[family] = self._apply_names(family, devices)
            settings = self.config_store.settings() if self.config_store else {}
            return {"connected": self.connected, "demo": self.demo, "complex": self.complex_name,
                    "home_name": settings.get("home_name", "우리 집"),
                    "connection_message": self.connection_message, "storage_warning": self.storage_warning,
                    "auth_warning": self.auth_warning, "last_auth_refresh": self.last_auth_refresh,
                    "devices": self.cache, "updated": self.updated, "errors": errors}

    def validate(self, action, devices=None):
        if not isinstance(action, dict):
            raise HomeError("제어 요청 형식이 다릅니다.")
        family, field, value = action.get("family"), action.get("field"), action.get("value")
        allowed = {"lights": {"OnOffStatus": ("On", "Off")},
                   "ventilation": {"PowerStatus": ("On", "Off"), "Mode": tuple(MODES), "WindPower": ("1", "2", "3")},
                   "heating": {"Heating": ("On", "Off"), "Mode": ("On", "Off"), "SettingTemp": None},
                   "hybrid": {"GasValveStatus": ("Close",), "InductionStatus": ("Off",)}}
        if family not in allowed or field not in allowed[family] or not isinstance(value, str):
            raise HomeError("허용되지 않은 제어입니다.")
        if allowed[family][field] is not None and value not in allowed[family][field]:
            raise HomeError("허용되지 않은 설정값입니다.")
        if devices is None:
            devices = self.devices(family)
        matches = [d for d in devices if d["group"] == str(action.get("group")) and d["sub"] == str(action.get("sub"))]
        if len(matches) != 1 or field not in matches[0]["states"]:
            raise HomeError("선택한 기기의 제어 항목을 찾지 못했습니다.")
        device = matches[0]
        if field in ("SettingTemp", "WindPower"):
            try:
                number = Decimal(value)
                minimum = Decimal(str(device["states"]["SettingMin"]))
                maximum = Decimal(str(device["states"]["SettingMax"]))
                if not all(v.is_finite() for v in (number, minimum, maximum)) or not minimum <= number <= maximum:
                    raise HomeError("기기의 설정 범위를 벗어났습니다.")
                if number != number.to_integral_value():
                    raise HomeError("정수 값만 입력해 주세요.")
                value = f"{number:.1f}" if field == "SettingTemp" else str(int(number))
            except (InvalidOperation, KeyError):
                raise HomeError("기기의 설정 범위를 확인하지 못했습니다.") from None
        clean = {"family": family, "group": device["group"], "sub": device["sub"], "field": field, "value": value}
        return clean, device

    def _send_control(self, action):
        if self.demo:
            for device in self.demo_devices[action["family"]]:
                if (device["group"], device["sub"]) == (action["group"], action["sub"]):
                    device["states"][action["field"]] = action["value"]
            return
        self._authenticated_request("/device/control", {
            "Device_Type": TYPES[action["family"]],
            "Group_Id": action["group"], "Sub_Id": action["sub"],
            "Status_Type": action["field"], "Status": action["value"]})

    def control(self, action):
        with self.lock:
            clean, device = self.validate(action)
            if clean["family"] == "ventilation" and clean["field"] == "Mode":
                if device["states"].get("Mode") == "Sleep" and clean["value"] not in ("Sleep", "Normal"):
                    self._send_control({**clean, "value": "Normal"})
                    for attempt in range(5):
                        if attempt:
                            time.sleep(1)
                        fresh = self.devices("ventilation")
                        matching = next((d for d in fresh if (d["group"], d["sub"]) == (clean["group"], clean["sub"])), None)
                        if matching and matching["states"].get("Mode") == "Normal":
                            break
                    else:
                        raise HomeError("일반 모드 전환이 확인되지 않아 다음 요청을 중단했습니다.", 409)
            self._send_control(clean)
            # A successful write must not become a failure just because readback fails.
            warning = None
            try:
                self.devices(clean["family"])
            except HomeError:
                warning = "요청은 전송했으나 재조회에 실패했습니다. 실제 기기를 확인해 주세요."
            return {"message": "제어 요청을 전송했습니다.", "warning": warning,
                    "devices": self.cache.get(clean["family"], [])}

    def elevator(self):
        with self.lock:
            if not self.connected:
                if not self.auto_connect or not self.restore_connection():
                    raise HomeError("기기 연동 인증이 필요합니다.", 409)
            if not self.demo:
                self._authenticated_request("/device/elevator-call", {})
            return {"message": "엘리베이터 호출 요청을 전송했습니다."}

    @staticmethod
    def _demo_devices():
        def d(group, sub, name, **states):
            return {"group": str(group), "sub": str(sub), "name": name, "states": states}
        return {
            "lights": [d(1, i, f"거실 조명{i}", OnOffStatus="On" if i < 4 else "Off") for i in range(1, 7)]
                      + [d(2, i, f"안방 조명{i}", OnOffStatus="Off") for i in range(1, 3)],
            "ventilation": [d(0, 1, "환기", PowerStatus="On", Mode="Pleasant", WindPower="1",
                              SettingMin="1", SettingMax="3", Filter="Normal", Pollution="Good")],
            "heating": [d(1, i, name, CurrentTemp=str(temp), SettingTemp="23.0", Heating="On" if i < 3 else "Off",
                          Mode="Off", SettingMin="5", SettingMax="45", Reservation="Off")
                        for i, (name, temp) in enumerate(zip(["거실", "침실1", "침실2", "침실3", "침실4"], [26, 24, 25, 23, 25]), 1)],
            "hybrid": [d(0, 1, "주방 안전", GasValveStatus="Close", InductionStatus="On", GasDetection="0")],
        }
