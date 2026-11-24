"""NAS web entry point. Run one process with Waitress, never Flask debug mode."""
import argparse
import atexit
import hmac
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session, send_file
from home_service import HomeError, HomeService
from schedules import Schedules
from config_store import ConfigStore, ConfigError
from usage_archive import UsageArchive


def create_app(config=None, service=None):
    config = config or {}
    demo = config.get("DEMO", os.environ.get("KDONE_DEMO") == "1")
    store = ConfigStore(config.get("CONFIG_DIR", os.environ.get("KDONE_CONFIG_DIR", str(Path(__file__).parent / "config"))))
    external, private = store.settings(), store.secrets()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("WEB_SECRET_KEY") or private.get("web_secret_key") or secrets.token_hex(32),
        ADMIN_PIN=os.environ.get("WEB_ADMIN_PIN") or private.get("web_admin_pin", ""), DEMO=demo,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=(os.environ.get("WEB_SECURE_COOKIE") == "1" if "WEB_SECURE_COOKIE" in os.environ
                               else external["secure_cookie"] if store.directory.joinpath("settings.json").exists() else False),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=16384,
        DATA_DIR=os.environ.get("KDONE_DATA_DIR", str(Path(__file__).parent / "data")),
        TEMPLATES_AUTO_RELOAD=demo,
        START_SCHEDULER=True)
    app.config.update(config)
    if not demo and (not isinstance(app.config["ADMIN_PIN"], str) or not re.fullmatch(r"[0-9]{6}", app.config["ADMIN_PIN"])):
        raise RuntimeError("config/secrets.json의 web_admin_pin 또는 WEB_ADMIN_PIN을 숫자 6자리 문자열로 설정하세요.")
    if not demo and store.directory.joinpath("secrets.json").exists() and not private.get("web_secret_key") and not os.environ.get("WEB_SECRET_KEY") and not config.get("SECRET_KEY"):
        raise RuntimeError("config/secrets.json의 web_secret_key에 무작위 비밀키를 설정하세요.")
    home = service or HomeService(demo=demo, config_store=store)
    jobs = Schedules(Path(app.config["DATA_DIR"]) / ("demo.sqlite3" if demo else "home.sqlite3"), home)
    app.extensions["home"] = home
    app.extensions["jobs"] = jobs
    app.extensions["config_store"] = store
    archive = UsageArchive(Path(app.config["DATA_DIR"]) / ("demo-usage.sqlite3" if demo or home.demo else "usage.sqlite3"), home)
    app.extensions["usage_archive"] = archive
    failures = {}
    rate_lock = threading.Lock()
    if app.config["START_SCHEDULER"]:
        if not service and not demo:
            home.restore_connection()
            home.start_maintenance()
            atexit.register(home.stop_maintenance)
        jobs.start()
        atexit.register(jobs.close)
        if not home.demo:
            archive.start()
            atexit.register(archive.close)

    @app.before_request
    def protect():
        if request.path.startswith("/api/external/"):
            supplied = request.headers.get("X-API-Key", "")
            scopes = None
            for item in store.secrets().get("api_keys", []):
                if item.get("enabled", True) and hmac.compare_digest(item["key"].encode(), supplied.encode()):
                    scopes = item.get("scopes", [])
                    break
            if scopes is None:
                raise HomeError("유효한 API 키가 필요합니다.", 401)
            segment = request.path[len("/api/external/"):].split("/")[0]
            required = {"state": "read", "schedules": "read", "control": "control", "timers": "timers", "automations": "automations"}.get(segment)
            if not required or required not in scopes:
                raise HomeError("이 API 키에 해당 권한이 없습니다.", 403)
            if request.method != "GET" and (not request.is_json or not isinstance(request.get_json(), dict)):
                raise HomeError("JSON 객체를 전송해 주세요.", 400)
            return  # Key authentication never creates a browser session.
        if request.path.startswith("/api/"):
            if request.method != "GET":
                expected = session.get("csrf", "")
                supplied = request.headers.get("X-CSRF-Token", "")
                if not expected or not hmac.compare_digest(expected, supplied):
                    raise HomeError("화면을 새로고침한 뒤 다시 시도해 주세요.", 403)
                if not request.is_json:
                    raise HomeError("JSON 요청만 허용됩니다.", 415)
                if not isinstance(request.get_json(), dict):
                    raise HomeError("JSON 객체를 전송해 주세요.")
            if request.path not in ("/api/session", "/api/login") and not (session.get("admin") or demo):
                raise HomeError("관리자 로그인이 필요합니다.", 401)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.errorhandler(HomeError)
    def known_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(ConfigError)
    def config_error(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(400)
    @app.errorhandler(413)
    @app.errorhandler(415)
    def input_error(error):
        return jsonify(error="요청 형식 또는 크기를 확인해 주세요."), error.code

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify(error="처리 중 오류가 발생했습니다. 기기 상태를 확인해 주세요."), 500

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/session")
    def session_info():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(csrf=session["csrf"], authenticated=bool(session.get("admin") or demo), demo=demo)

    @app.post("/api/login")
    def login():
        address = request.remote_addr or "local"
        now = time.monotonic()
        with rate_lock:
            attempts = [t for t in failures.get(address, []) if t > now - 300]
            if len(attempts) >= 10:
                raise HomeError("로그인 시도가 많습니다. 5분 뒤 다시 시도해 주세요.", 429)
            supplied = request.get_json().get("pin", "")
            if not isinstance(supplied, str) or not re.fullmatch(r"[0-9]{6}", supplied) or not hmac.compare_digest(supplied.encode(), app.config["ADMIN_PIN"].encode()):
                failures[address] = attempts + [now]
                raise HomeError("6자리 PIN을 확인해 주세요.", 401)
            failures.pop(address, None)
        session.clear()
        session.update(admin=True, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return jsonify(csrf=session["csrf"])

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(message="관리자 화면에서 로그아웃했습니다. 기기 연결과 예약은 유지됩니다.")

    @app.get("/api/complexes")
    def complexes():
        return jsonify(items=home.complexes(request.args.get("q", "")))

    @app.get("/api/settings")
    def settings_info():
        return jsonify(store.public())

    @app.get("/api/usage")
    def usage_info():
        return jsonify(archive.query(request.args.get("category"), request.args.get("period"),
                                         request.args.get("selected"), request.args.get("compare", "0")))

    @app.get("/api/usage/status")
    def usage_status():
        return jsonify(archive.status())

    @app.get("/api/usage/reviews")
    def usage_reviews():
        return jsonify(items=archive.reviews())

    @app.get("/api/usage/history")
    def usage_history():
        return jsonify(archive.history(request.args.get('category'),request.args.get('day')))

    @app.get("/api/usage/export")
    def usage_export():
        return send_file(archive.export(),mimetype='application/zip',as_attachment=True,download_name='kdone-usage-export.zip')

    @app.post("/api/usage/refresh")
    def usage_refresh():
        return jsonify(archive.refresh(request.get_json().get('category')))

    @app.post("/api/usage/retry")
    def usage_retry():
        return jsonify(archive.retry())

    @app.post("/api/usage/edit")
    def usage_edit():
        return jsonify(archive.edit(request.get_json()))

    @app.post("/api/usage/resolve")
    def usage_resolve():
        return jsonify(archive.resolve(request.get_json()))

    @app.post("/api/connection/saved/request")
    def saved_request():
        home.request_saved_code()
        return jsonify(message="저장된 계정으로 인증번호를 요청했습니다. 월패드를 확인하세요.")

    @app.post("/api/connection/saved/restore")
    def saved_restore():
        home.auto_connect = True
        if not home.restore_connection():
            raise HomeError(home.connection_message or "저장된 인증정보로 연결하지 못했습니다.", 409)
        return jsonify(message="저장된 인증정보로 연결했습니다.")

    @app.post("/api/connection/request")
    def request_code():
        data = request.get_json()
        home.request_code(data.get("complex_id"), data.get("username"), data.get("password"), data.get("complex_name", ""))
        return jsonify(message="인증번호를 요청했습니다. 월패드를 확인해 주세요.")

    @app.post("/api/connection/certify")
    def certify():
        code = request.get_json().get("code")
        if not isinstance(code, str) or not code.strip():
            raise HomeError("인증번호를 입력해 주세요.")
        home.certify(code.strip())
        return jsonify(message="기기 연동이 완료됐습니다.", warning=home.storage_warning)

    @app.post("/api/connection/disconnect")
    def disconnect():
        if request.get_json().get("confirm") is not True:
            raise HomeError("연결 해제 확인이 필요합니다.")
        with home.lock:
            if not home.demo:
                home.auto_connect = False
                home.api = home.pending = None
                home.cache.clear()
        return jsonify(message="기기 연결을 해제했습니다. 예약은 재인증 후 실행됩니다.")

    @app.get("/api/state")
    def state():
        return jsonify(home.state(refresh=request.args.get("refresh") == "1"))

    @app.post("/api/control")
    def control():
        data = request.get_json()
        if data.get("confirm") is not True:
            raise HomeError("제어 확인이 필요합니다.")
        return jsonify(home.control(data.get("action")))

    @app.post("/api/elevator")
    def elevator():
        if request.get_json().get("confirm") is not True:
            raise HomeError("호출 확인이 필요합니다.")
        return jsonify(home.elevator())

    @app.get("/api/external/state")
    def external_state():
        return jsonify(home.state(refresh=True))

    @app.get("/api/external/schedules")
    def external_schedules():
        return jsonify(timers=jobs.timer_list(), automations=jobs.automation_list())

    @app.post("/api/external/control")
    def external_control():
        return jsonify(home.control(request.get_json().get("action")))

    @app.post("/api/external/timers")
    def external_timer():
        data = request.get_json()
        return jsonify(id=jobs.save_timer_sequence(data.get("sequence"), data.get("label")))

    @app.post("/api/external/automations/<automation_id>/run")
    def external_run(automation_id):
        return jsonify(id=jobs.run_automation(automation_id), message="자동화를 실행 대기열에 넣었습니다.")

    @app.get("/api/schedules")
    def schedule_list():
        return jsonify(items=jobs.list(), timers=jobs.timer_list(), automations=jobs.automation_list(), connected=home.connected)

    @app.post("/api/timers")
    def timer_add():
        data = request.get_json()
        if "sequence" in data:
            return jsonify(id=jobs.save_timer_sequence(data.get("sequence"), data.get("label")))
        return jsonify(id=jobs.add_timer(data.get("action"), data.get("duration"), data.get("setup")))

    @app.post("/api/timers/<timer_id>/edit")
    def timer_edit(timer_id):
        data = request.get_json()
        return jsonify(id=jobs.save_timer_sequence(data.get("sequence"), data.get("label"), timer_id))

    @app.post("/api/timers/<timer_id>/stop")
    def timer_stop(timer_id):
        jobs.stop_timer(timer_id)
        return jsonify(message="타이머를 종료했습니다.")

    @app.post("/api/automations")
    def automation_add():
        data = request.get_json()
        if "sequence" in data:
            return jsonify(id=jobs.add_sequence(data.get("sequence"), data.get("rule"), data.get("label")))
        return jsonify(id=jobs.add_automation(data.get("action"), data.get("rule"), data.get("duration"), data.get("setup")))

    @app.post("/api/automations/<automation_id>/<operation>")
    def automation_update(automation_id, operation):
        if operation == "edit":
            data = request.get_json()
            jobs.edit_automation(automation_id, data.get("sequence"), data.get("rule"), data.get("label"))
            return jsonify(message="자동화를 수정했습니다. 진행 중인 회차는 기존 설정으로 마칩니다.")
        if operation not in ("toggle", "delete"):
            raise HomeError("지원하지 않는 동작입니다.", 404)
        jobs.set_automation(automation_id, request.get_json().get("enabled"), delete=operation == "delete")
        return jsonify(message="자동화를 변경했습니다.")

    @app.post("/api/schedules")
    def schedule_add():
        data = request.get_json()
        if data.get("confirm") is not True:
            raise HomeError("예약 확인이 필요합니다.")
        due = None if data.get("start_now") is True else data.get("due")
        if due is None and data.get("start_now") is not True:
            raise HomeError("예약 시각을 입력해 주세요.")
        job_id = jobs.add(data.get("action"), due, data.get("duration", 0), data.get("label", "예약"), data.get("setup"))
        return jsonify(id=job_id, message="예약을 저장했습니다.")

    @app.post("/api/schedules/<job_id>/cancel")
    def schedule_cancel(job_id):
        jobs.cancel(job_id)
        return jsonify(message="예약을 취소했습니다. 현재 기기 상태는 바꾸지 않습니다.")

    return app


if __name__ == "__main__":
    from waitress import serve
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="서버 통신 없는 데모")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    app = create_app({"DEMO": args.demo or os.environ.get("KDONE_DEMO") == "1"})
    print(f"KDOne Home: http://{args.host}:{args.port}" + (" [DEMO]" if app.config["DEMO"] else ""), flush=True)
    serve(app, host=args.host, port=args.port, threads=8)
