from unittest.mock import Mock

import pytest

from config_store import ConfigStore
from home_service import HomeError, HomeService


def service(tmp_path):
    store = ConfigStore(tmp_path)
    store.save_account({"complex_id": "100118", "username": "user", "password": "password", "app_certify": "code"})
    home = HomeService(config_store=store)
    home.api = Mock(access_token="old", refresh_token="refresh", complex_id="100118")
    return home


def test_periodic_refresh_changes_session_without_wallpad(tmp_path, monkeypatch):
    home = service(tmp_path)
    candidate = Mock(access_token="new", complex_id="100118")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    monkeypatch.setattr("home_service.time.monotonic", lambda: 10000)
    home.maintain_connection()
    assert home.api is candidate
    assert home.last_auth_refresh
    candidate.get_token.assert_called_once()
    candidate._get_certification_code.assert_not_called()
    home.maintain_connection()
    candidate.get_token.assert_called_once()


def test_refresh_failure_preserves_active_session(tmp_path, monkeypatch):
    home = service(tmp_path)
    old = home.api
    candidate = Mock()
    candidate.get_token.side_effect = HomeError("server unavailable")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    assert not home.restore_connection(force=True)
    assert home.api is old and home.connected and home.auth_warning
    candidate._get_certification_code.assert_not_called()


def test_rejected_read_refreshes_then_retries_once(tmp_path, monkeypatch):
    home = service(tmp_path)
    old = home.api
    old._send_request.side_effect = HomeError("expired", remote_rejected=True)
    candidate = Mock(access_token="new", complex_id="100118")
    candidate._send_request.return_value = {"Resources": []}
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    assert home.devices("lights") == []
    candidate.get_token.assert_called_once()
    old._send_request.assert_called_once()
    candidate._send_request.assert_called_once()


def test_uncertain_write_timeout_never_repeats(tmp_path, monkeypatch):
    home = service(tmp_path)
    home.api._send_request.side_effect = HomeError("timeout", 504)
    restore = Mock()
    monkeypatch.setattr(home, "restore_connection", restore)
    with pytest.raises(HomeError):
        home._authenticated_request("/device/control", {"Status": "On"})
    restore.assert_not_called()
    home.api._send_request.assert_called_once()


def test_auth_rejected_write_refreshes_once(tmp_path, monkeypatch):
    home = service(tmp_path)
    old = home.api
    old._send_request.side_effect = HomeError("unauthorized", auth_rejected=True)
    candidate = Mock(access_token="new", complex_id="100118")
    monkeypatch.setattr("home_service.HomeAPI", Mock(return_value=candidate))
    home._authenticated_request("/device/control", {"Status": "On"})
    candidate.get_token.assert_called_once()
    candidate._send_request.assert_called_once()


def test_manual_disconnect_and_retry_cooldown(tmp_path, monkeypatch):
    home = service(tmp_path)
    refresh = Mock()
    monkeypatch.setattr(home, "restore_connection", refresh)
    home.auto_connect = False
    home.maintain_connection()
    refresh.assert_not_called()
    home.auto_connect = True
    home.auth_warning = "network error"
    home.last_auth_attempt = 1000
    monkeypatch.setattr("home_service.time.monotonic", lambda: 1030)
    home.maintain_connection()
    refresh.assert_not_called()
    monkeypatch.setattr("home_service.time.monotonic", lambda: 1061)
    home.maintain_connection()
    refresh.assert_called_once_with(force=True)


def test_expired_pending_code_does_not_disable_maintenance(tmp_path, monkeypatch):
    home = service(tmp_path)
    home.pending = (Mock(), 1000, "home")
    monkeypatch.setattr("home_service.time.monotonic", lambda: 10000)
    monkeypatch.setattr(home, "restore_connection", Mock())
    home.maintain_connection()
    assert home.pending is None
    home.restore_connection.assert_called_once_with(force=True)
