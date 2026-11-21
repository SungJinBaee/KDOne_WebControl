import json
from unittest.mock import Mock

import pytest

from config_store import ConfigError, ConfigStore
from home_service import HomeService, HomeError
from webapp import create_app


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_device_names_reload_without_restart_and_revert(tmp_path):
    store = ConfigStore(tmp_path)
    home = HomeService(demo=True, config_store=store)
    original = home.devices("lights")[0]["name"]
    write(tmp_path / "settings.json", {"home_name": "My home", "device_names": {"lights/1/1": "Reading lamp"}})
    state = home.state()
    assert state["home_name"] == "My home"
    assert state["devices"]["lights"][0]["name"] == "Reading lamp"
    assert state["devices"]["lights"][1]["name"] != "Reading lamp"
    write(tmp_path / "settings.json", {"device_names": {}})
    assert home.state()["devices"]["lights"][0]["name"] == original


def test_external_web_password_and_public_settings_hide_secrets(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    write(config / "settings.json", {"secure_cookie": False})
    private = {"web_admin_password": "long-private-password", "web_secret_key": "private-session-key",
               "account": {"username": "PRIVATE_USER", "password": "PRIVATE_PASSWORD", "app_certify": "PRIVATE_CODE"}}
    write(config / "secrets.json", private)
    app = create_app({"TESTING": True, "DEMO": False, "CONFIG_DIR": str(config), "DATA_DIR": str(tmp_path / "data"), "START_SCHEDULER": False})
    try:
        client = app.test_client()
        assert client.get("/api/settings").status_code == 401
        csrf = client.get("/api/session").json["csrf"]
        response = client.post("/api/login", json={"password": private["web_admin_password"]}, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 200
        public = client.get("/api/settings")
        assert public.json["saved_account"] and public.json["saved_certification"]
        for secret in ("PRIVATE_USER", "PRIVATE_PASSWORD", "PRIVATE_CODE", "private-session-key", "long-private-password"):
            assert secret not in public.text
        assert client.get("/config/secrets.json").status_code == 404
    finally:
        app.extensions["jobs"].close()


def test_certification_saved_outside_image_and_restored_without_requesting_code(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.save_account({})
    candidate = Mock(username="user", password="password", app_certify="123456", access_token="TOKEN", refresh_token="REFRESH")
    factory = Mock(return_value=candidate)
    monkeypatch.setattr("home_service.HomeAPI", factory)
    home = HomeService(config_store=store)
    home.request_code("100118", "user", "password", "test complex")
    home.certify("123456")
    saved = store.secrets()
    assert saved["account"]["app_certify"] == "123456"
    assert "TOKEN" not in json.dumps(saved) and "REFRESH" not in json.dumps(saved)
    candidate.reset_mock()
    restored = HomeService(config_store=store)
    assert restored.restore_connection()
    candidate.get_token.assert_called_once()
    candidate._get_certification_code.assert_not_called()
    assert restored.connected


def test_expired_saved_auth_does_not_send_new_wallpad_code(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.save_account({"username": "user", "password": "password", "complex_id": "100118", "app_certify": "expired"})
    candidate = Mock()
    candidate.get_token.side_effect = HomeError("expired")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    home = HomeService(config_store=store)
    assert not home.restore_connection()
    assert not home.connected
    assert home.connection_message
    candidate._get_certification_code.assert_not_called()


def test_invalid_json_never_echoes_secret(tmp_path):
    (tmp_path / "secrets.json").write_text('{"password":"SUPER_SECRET" broken', encoding="utf-8")
    with pytest.raises(ConfigError) as error:
        ConfigStore(tmp_path).secrets()
    assert "SUPER_SECRET" not in str(error.value)


def test_saved_account_write_preserves_web_credentials(tmp_path):
    write(tmp_path / "secrets.json", {"web_admin_password": "keep", "web_secret_key": "keep-key"})
    store = ConfigStore(tmp_path)
    store.save_account({"username": "new-user", "password": "new-password", "access_token": "NEVER_STORE"})
    data = store.secrets()
    assert data["web_admin_password"] == "keep" and data["web_secret_key"] == "keep-key"
    assert "NEVER_STORE" not in json.dumps(data)
    assert not list(tmp_path.glob(".secrets-*"))


def test_storage_failure_keeps_successful_connection(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    candidate = Mock(username="user", password="password", app_certify="code", access_token="TOKEN")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    monkeypatch.setattr(store, "save_account", Mock(side_effect=PermissionError("denied")))
    home = HomeService(config_store=store)
    home.request_code("100118", "user", "password")
    home.certify("code")
    assert home.connected and home.storage_warning
