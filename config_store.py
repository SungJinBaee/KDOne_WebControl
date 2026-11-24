"""External JSON configuration. Secrets are never returned to the browser."""
import copy
import json
import os
import tempfile
import threading
from pathlib import Path

DEFAULTS = {"home_name": "우리 집", "complex_id": "100118", "complex_name": "호반써밋동탄",
            "secure_cookie": True, "device_names": {}, "session_refresh_minutes": 15}


class ConfigError(Exception):
    pass


class ConfigStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()

    def read(self, filename):
        with self.lock:
            path = self.directory / filename
            if not path.exists():
                return {}
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                raise ConfigError(f"{filename}을 읽지 못했습니다. 파일 권한과 JSON 형식을 확인하세요.") from None
            if not isinstance(data, dict):
                raise ConfigError(f"{filename}의 최상위 값은 JSON 객체여야 합니다.")
            return data

    def settings(self):
        data = {**copy.deepcopy(DEFAULTS), **self.read("settings.json")}
        if not all(isinstance(data[key], str) for key in ("home_name", "complex_id", "complex_name")):
            raise ConfigError("settings.json의 집 이름과 단지 정보는 문자열이어야 합니다.")
        if not isinstance(data["secure_cookie"], bool):
            raise ConfigError("secure_cookie는 true 또는 false여야 합니다.")
        names = data["device_names"]
        if type(data["session_refresh_minutes"]) is not int or not 1 <= data["session_refresh_minutes"] <= 1440:
            raise ConfigError("session_refresh_minutes는 1~1440 사이의 정수여야 합니다.")
        if not isinstance(names, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               or not v.strip() or len(v) > 100 for k, v in names.items()):
            raise ConfigError("device_names는 기기 키와 표시 이름의 문자열 목록이어야 합니다.")
        return data

    def secrets(self):
        data = self.read("secrets.json")
        keys = data.get("api_keys", [])
        if not isinstance(keys, list) or len(keys) > 32:
            raise ConfigError("api_keys는 최대 32개 키의 목록이어야 합니다.")
        for item in keys:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("key"), str) or len(item["key"]) < 32 or type(item.get("enabled", True)) is not bool:
                raise ConfigError("API 키에는 name과 32자 이상의 key가 필요합니다.")
            scopes = item.get("scopes", [])
            if not isinstance(scopes, list) or any(scope not in ("read", "control", "timers", "automations") for scope in scopes):
                raise ConfigError("API 키 권한은 read, control, timers, automations 중 지정하세요.")
        for key in ("web_admin_pin", "web_admin_password", "web_secret_key"):
            if key in data and not isinstance(data[key], str):
                raise ConfigError(f"secrets.json의 {key}는 문자열이어야 합니다.")
        account = data.get("account", {})
        if not isinstance(account, dict) or any(not isinstance(value, str) for value in account.values()):
            raise ConfigError("secrets.json의 account 값은 문자열이어야 합니다.")
        return data

    def save_account(self, account):
        with self.lock:
            data = self.secrets()
            data["account"] = {key: account.get(key, "") for key in (
                "complex_id", "complex_name", "username", "password", "app_certify")}
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".secrets-", dir=self.directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(data, file, ensure_ascii=False, indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.directory / "secrets.json")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def public(self):
        settings = self.settings()
        account = self.secrets().get("account", {})
        return {"home_name": settings["home_name"], "complex_id": settings["complex_id"],
                "complex_name": settings["complex_name"], "device_names": settings["device_names"],
                "saved_account": bool(account.get("username") and account.get("password")),
                "saved_certification": bool(account.get("app_certify"))}
