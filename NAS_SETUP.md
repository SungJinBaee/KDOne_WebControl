# KDOne 웹 관리 · NAS 배포

## 사용량 메뉴 업데이트

### 저장·검토 방식으로 전환

중복 검토 처리 수정은 `usage_archive.py`와 화면의 `templates/index.html`, `static/app.js`를 함께 적용합니다. Web Station에는 최신 화면 ZIP을 적용합니다. 사유 입력 후 기존 값 유지 또는 새 값 채택을 누르면 동일 후보 건을 함께 처리하고 원본 이력은 남깁니다. 확정값이 이미 다른 값으로 수정된 경우에는 기존 충돌 보호를 유지하므로 목록 위 오류를 확인하고 일별 이력을 조회하세요.

조회·집계 시점의 한국 날짜 기준 오늘과 어제는 20% 변동 보류에서 제외합니다. 직접 수정값·단위·누락·숫자 오류 검토는 그대로 유지하며, 날짜가 이틀 이상 지난 기록에 20% 기준을 적용합니다. 이 정책 수정만 적용할 때는 `usage_archive.py`를 교체하고 재빌드하면 됩니다. 화면 안내 문구는 최신 화면 ZIP에 포함됩니다.

최신 NAS ZIP에서 `usage_archive.py`(신규), `webapp.py`, `energy_usage.py`, `Dockerfile`, `templates/index.html`, `static/app.js`, `static/app.css`를 적용하세요. Web Station에는 최신 화면 ZIP 전체를 적용하며 `api.php`도 반드시 교체합니다. 기존 계정 설정, Compose 데이터 볼륨과 예약 DB는 유지합니다. 이후 `sudo docker compose up -d --build --force-recreate kdone`로 반영합니다.

실행 중인 API가 기존 세대에 연결되면 2023년부터 현재까지 5종 사용량을 연도별 작업으로 자동 수집합니다. 수집 상태에서 대기·진행·완료·빈 구간·실패를 구분해 확인할 수 있습니다. 서버가 제공하지 않는 기간을 만들어 채우지 않습니다. 실패·빈 구간 재수집 버튼으로 다시 시도합니다. 중간 재시작은 완료한 작업을 유지하고 진행 중이던 구간부터 재개합니다. 최초 수집 중 화면은 일부 기록만 표시할 수 있습니다.

매일 03:00 한국 시간에 어제부터 과거 3일을 재수집합니다. NAS 중지 기간이 길면 마지막 완료한 정기 수집 다음 날부터 누락 구간도 따라잡습니다. 웹 조회 버튼은 선택한 항목의 오늘 포함 3일을 수집한 뒤 저장된 확정값으로 집계합니다. 수집 중에는 최대 45초 대기하며 수집 실패·경합 시에는 사유와 함께 저장된 집계로 표시합니다. 수집 자체는 현재 토큰만 사용하고 로그인·월패드 재인증을 요청하지 않습니다.

20% 이상 절대 변동은 보류하고 검토 대기로 남깁니다. 0→양수·단위 변경·기록 누락·숫자/날짜 오류도 보류합니다. 사유를 입력해 새 값 채택 또는 기존 값 유지를 선택하세요. 잘못된 원본 수치는 바로 채택할 수 없고 직접 수정해야 합니다. 직접 수정하려면 항목과 날짜 선택 → 이력 조회 → 값·단위·사유 입력 → 수정값 저장 순서입니다. 수정값은 이후 자동 수집으로 바뀌지 않습니다. 월별 값은 일별 수정값을 합산하므로 월 합계를 직접 수정하지 않습니다.

영구 DB는 컨테이너의 `/app/data/usage.sqlite3`이며 기존 Compose의 `kdone-data` 볼륨에 저장됩니다. `docker compose down -v`로 볼륨을 삭제하면 사용량과 예약 이력도 사라지므로 기존 볼륨을 유지합니다. 데모는 별도 `demo-usage.sqlite3`를 사용합니다. 화면의 저장 데이터·이력 다운로드는 확정값·원본 관측값·수정/검토 이력·수집 작업을 CSV ZIP으로 내보냅니다. 전체 환경 복구용 백업은 컨테이너를 중지한 상태에서 데이터 볼륨과 config 폴더를 함께 보관하세요. CSV ZIP은 DB 복원 파일이 아닙니다.

전년 조회가 계속 23일/11월에서 끝나면 실행 중인 이미지가 이전 코드인지 확인하세요. NAS의 `energy_usage.py`를 최신 파일로 교체한 뒤 `sudo docker compose up -d --build --force-recreate kdone`로 재빌드·재생성합니다. Web Station 파일만 교체하면 Python 조회 범위는 변경되지 않습니다.

다음 명령은 인증 요청 없이 현재 컨테이너의 기간 계산만 확인합니다. 출력은 `2025-11-30`이어야 합니다.

```bash
sudo docker compose exec -T kdone python -c 'from datetime import date; from energy_usage import query_ranges; print(query_ranges("Electricity", "Day", "2026-11", "1", today=date(2026,11,23))[1][2])'
```

`/volume1/docker/kdone-home/`의 `webapp.py`, `home_service.py`, 새 파일 `energy_usage.py`, `Dockerfile`을 교체하고 `templates/index.html`, `static/app.js`, `static/app.css`도 함께 적용합니다. Web Station 사이트에는 별도 `kdone-webstation.zip`의 `index.html`, `static/`, `api.php`를 함께 덮어씁니다. API 프록시에 `usage` 경로가 추가되어 PHP도 교체해야 합니다. 실제 `config/`와 `data/`는 유지하세요.

프로젝트 폴더에서 `sudo docker compose up -d --build`로 반영하고 시작 로그를 확인합니다. 로그인 후 사용량 메뉴에서 전기 일별 조회부터 확인하고 경동원 앱의 같은 기간 기록과 비교하세요. 이어서 월별·전년 비교와 수도·난방·온수·가스를 확인합니다. 단지의 실사용량 응답은 아직 현장에서 검증해야 합니다. 실패 시 화면 오류만 공유하고 인증 토큰·계정 정보는 공유하지 마세요.

Web Station에서 HTML을 직접 제공하는 분리 구성은 [WEB_STATION_SETUP.md](WEB_STATION_SETUP.md)를 참고하세요.
화면 ZIP은 `artifacts/kdone-webstation.zip`이며 실제 계정·설정 파일은 포함하지 않습니다.

## 외부 설정 방식으로 변경됨 — 이 절을 우선 적용

설정과 개인정보는 Docker 이미지에 포함하지 않습니다. `./config` 폴더를 컨테이너의
`/app/config`에 연결합니다. **현재 Compose는 config JSON 파일을 사용합니다.**

### 최초 설정 / 기존 배포 업데이트

1. 새 압축파일로 소스를 업데이트합니다. 기존 `config/settings.json`, `config/secrets.json`과
   예약 볼륨은 덮어쓰거나 삭제하지 마세요. ZIP에는 `.example.json`만 포함됩니다.
2. `config/settings.example.json`을 `config/settings.json`으로 복사합니다.
3. `config/secrets.example.json`을 `config/secrets.json`으로 복사합니다.
4. `secrets.json`의 `web_admin_pin`을 숫자 6자리 문자열로 직접 설정하고, `web_secret_key`에 무작위 값을 넣습니다. 기존 설치는 `web_admin_password` 대신 `web_admin_pin`을 추가하세요. 월패드 `account`와 기존 비밀키는 유지합니다.
   기존 `.env`를 사용했다면 관리자 비밀번호와 비밀키를 이 두 항목으로 옮깁니다.
5. 컨테이너는 UID 1000으로 실행됩니다. `config` 폴더와 파일에 해당 UID의 읽기·쓰기 권한이 필요합니다.
   SSH에서 프로젝트 폴더를 확인한 뒤 다음처럼 설정할 수 있습니다.

```sh
cd /volume1/docker/kdone-home
sudo chown -R 1000:1000 ./config
sudo chmod 700 ./config
sudo chmod 600 ./config/settings.json ./config/secrets.json
```

웹에서 인증 성공 시 `secrets.json`을 원자적으로 교체하므로 폴더에도 쓰기 권한이 필요합니다.
File Station이나 SSH로 설정을 수정할 NAS 관리자에게도 필요한 접근 권한을 유지하세요.
`config` 폴더는 Web Station의 공개 정적 문서 경로에 넣지 마세요.

이 구조를 적용하는 **최초 한 번만 이미지 재빌드·컨테이너 재생성**이 필요합니다.

```sh
docker compose up -d --build
```

### 설정별 반영 방법

| 설정 | 파일 | 반영 |
|---|---|---|
| 집 이름, 단지 검색 기본값 | `config/settings.json` | 브라우저 새로고침 |
| 조명·방·환기 이름 | `config/settings.json`의 `device_names` | 상태 새로고침, 이미지 재빌드·서버 재시작 불필요 |
| NAVIEN 계정·단지·저장 인증값 | `config/secrets.json`의 `account` | 연결 해제 후 **저장된 인증정보로 연결** |
| 인증정보가 없거나 만료됨 | 같은 파일 + 화면의 월패드 인증 | **외부 파일의 계정으로 인증번호 요청** → 새 번호 입력 |
| 관리자 비밀번호, 웹 세션 비밀키 | `config/secrets.json` | `docker compose restart kdone` |
| HTTPS 쿠키 설정 | `config/settings.json`의 `secure_cookie` | `docker compose restart kdone` |
| 포트·마운트 경로 | `compose.yaml` | `docker compose up -d --no-build` |

평소 JSON 설정을 바꾼 뒤에는 `--build`를 사용하지 않습니다.
모든 기기 제어와 엘리베이터 호출은 확인창 없이 즉시 실행합니다. 예약 취소·연결 해제·로그아웃의 관리 확인창은 유지합니다.
현재 Compose는 `./static`도 읽기 전용으로 연결합니다. 기존 배포에서 이 구성을 적용하려면
`compose.yaml`과 `static/app.js`를 업데이트하고 `docker compose up -d --no-build`로
컨테이너를 재생성하세요. 이후 JavaScript·CSS 수정은 파일 교체와 브라우저 새로고침으로 반영됩니다.
컨테이너 재생성 중 잠시 서비스가 중단되며 저장된 인증정보로 재연결을 시도합니다.
설정 파일 형식이 잘못되면 파일 이름과 오류 안내만 표시하며 비밀값을 로그에 출력하지 않습니다.

### 기기 이름 키

`기기종류/Group_Id/Sub_Id` 형식으로 지정합니다. 생략된 기기는 서버 이름을 사용합니다.

```json
{
  "home_name": "우리 집",
  "complex_id": "100118",
  "complex_name": "호반써밋동탄",
  "secure_cookie": true,
  "device_names": {
    "lights/1/1": "거실 메인 조명",
    "lights/1/2": "거실 간접 조명",
    "heating/1/2": "안방",
    "ventilation/0/1": "전열교환기"
  }
}
```

기기종류는 `lights`, `heating`, `ventilation`, `hybrid`입니다. `secure_cookie`는 HTTPS에서 `true`,
PC의 HTTP 시험에서는 `false`로 설정합니다.

### 계정과 인증정보

```json
{
  "web_admin_pin": "012345",
  "web_secret_key": "생성한-무작위-비밀키",
  "account": {
    "complex_id": "100118",
    "complex_name": "호반써밋동탄",
    "username": "본인-아이디",
    "password": "본인-비밀번호",
    "app_certify": ""
  }
}
```

`app_certify`는 처음에는 빈 값으로 두고 화면에서 월패드 인증을 완료하세요.
인증 성공 시 입력 계정과 인증값이 이 외부 파일에 저장되며 다음 시작 때 재연결을 시도합니다.
**저장한다고 인증번호의 만료가 없어지는 것은 아닙니다.** 재사용을 서버가 거부하면 새 월패드 인증이 필요합니다.
접속·갱신 토큰은 파일에 저장하지 않고 메모리에만 유지합니다.
서버는 브라우저 접속 여부와 관계없이 기본 15분마다 저장된 인증값으로 `get_token()`을 호출해
새 접속 토큰 발급을 시도합니다. `settings.json`의 `session_refresh_minutes`를 1~1440 범위에서
수정하면 재빌드·재시작 없이 다음 점검부터 반영됩니다. 15분은 프로그램의 기본 정책이며 서버가 공지한 만료 시간은 아닙니다.
갱신 실패 시 기존 연결을 유지하고 1분 간격으로 재시도합니다. 연결 설정에서 최근 인증 갱신 시각을 볼 수 있습니다.
조회가 서버에서 거부되면 자동 갱신 후 조회를 한 번 재시도합니다. 제어 명령은 명시적인 HTTP 401/403
거부일 때만 갱신 후 한 번 재시도하며, 시간 초과 등 결과가 불명확하면 중복 전송하지 않습니다.
자동 처리는 새 월패드 번호를 요청하지 않습니다. 사용자가 연결 해제를 선택하면 자동 연결도 정지합니다.
서버가 저장된 인증값을 취소·만료시키는 경우까지 무기한 연결을 보장하지는 못합니다.
실제 NAS에서 장시간 유지와 외부 접속을 시험해 주세요. NAS 전원·인터넷 장애나 서버 정책 변경 시 제어가 중단될 수 있습니다.
인증은 완료됐지만 파일 저장에 실패하면 화면에 쓰기 권한 안내를 표시합니다.
`secrets.json`은 비밀번호가 들어 있는 평문 파일이므로 NAS 파일 권한과 백업 접근을 제한하고 공유하지 마세요.

환경 변수 `WEB_ADMIN_PIN`, `WEB_SECRET_KEY`, `WEB_SECURE_COOKIE`를 직접 지정한 경우에는
호환성을 위해 파일보다 우선합니다. 현재 Compose는 이 변수들을 주입하지 않습니다.
PC에서 파일 설정을 시험할 때는 이전 PowerShell 세션에 설정한 변수가 남아 있지 않은지 확인하세요.

Windows 직접 실행도 프로젝트의 `config`를 사용합니다. 다른 경로를 쓰려면 `KDONE_CONFIG_DIR` 환경 변수를 지정합니다.

외부 폴더 연결 방식 참고: [Docker bind mounts 공식 문서](https://docs.docker.com/engine/storage/bind-mounts/).

대상 환경: 사용자 제공 **DS216+II / DSM 7.4-90075**. 실제 NAS에 접속하거나 배포하지는 않았습니다.
패키지 이름과 화면은 설치 상태에 따라 다르므로 Container Manager 또는 Docker 설치 가능 여부부터 확인하세요.

## 이번 NAS 테스트: Container Manager 설치됨

1. PC의 `artifacts/kdone-home-nas.zip`을 NAS File Station으로 업로드합니다.
2. `/volume1/docker/kdone-home`에 압축을 풉니다. 이 폴더 바로 아래에 `compose.yaml`과 `Dockerfile`이 있어야 합니다.
3. 위 외부 설정 절대로 `config/settings.json`, `config/secrets.json`을 만들고 관리자 비밀번호·비밀키를 설정합니다.
   관리자 PIN은 숫자 6자리 문자열, 비밀키는 아래 생성 명령으로 만든 값을 사용합니다.
4. Container Manager → 프로젝트 → 생성에서 이름을 `kdone-home`, 경로를 위 폴더로 지정하고
   기존 `compose.yaml`을 사용하여 빌드·시작합니다. 설정을 찾지 못하면 config 파일 이름·권한·마운트 위치를 확인합니다.
5. Web Station 웹 포털에서 컨테이너의 8088 서비스로 연결하거나, 아래 HTTPS 역방향 프록시를 설정합니다.
6. 웹 관리자 로그인 → 연결 설정 → 단지 검색 → NAVIEN 계정 입력 → 월패드 인증 → 조회부터 테스트합니다.

HTTPS 역방향 프록시를 별도 포트로 시험하는 예:

| 항목 | 값 |
|---|---|
| 원본 프로토콜 / 호스트 / 포트 | HTTPS / 사용 중인 NAS 도메인 / 8443 |
| 대상 프로토콜 / 호스트 / 포트 | HTTP / 127.0.0.1 / 8088 |
| 접속 주소 | `https://사용중인-NAS도메인:8443` |

원본에는 실제로 NAS로 연결되는 도메인과 해당 인증서를 사용하세요. DSM 방화벽을 사용하는 경우
테스트 기기에서 원본 포트로 접근할 수 있어야 합니다. 내부 테스트를 위해 공유기 포트 포워딩은 필요하지 않습니다.
기존 443 포트의 서비스와 충돌을 피하려고 예시에 8443을 사용했으며, 이미 사용 중이라면 다른 포트를 선택합니다.

`http://NAS_IP:8088`로는 접속되지 않는 것이 기본 구성의 정상 동작입니다.
관리자 페이지가 열리지만 로그인이 유지되지 않는다면 HTTPS 여부와 `WEB_SECURE_COOKIE` 설정을 확인하세요.

빌드 실패 시 Container Manager의 빌드 로그, 시작 후 종료되면 컨테이너 로그를 확인합니다.
빌드에는 `python:3.12-slim` 이미지와 PyPI 패키지를 다운로드할 NAS 인터넷 연결이 필요합니다.
로그를 공유할 때 `.env`, 비밀번호, 인증번호와 토큰은 포함하지 마세요.

PC에서 최신 소스로 배포 압축파일을 다시 만들려면:

```powershell
.\.venv\Scripts\python.exe package_nas.py
```

이 패키지는 소스만 포함합니다. 예약 데이터, `.env`, 계정, 토큰, 가상환경은 포함하지 않습니다.

## 구조

```text
PC / iPhone 브라우저
        │ HTTPS
Web Station 웹 포털 또는 DSM 역방향 프록시
        │ HTTP · NAS 내부 127.0.0.1:8088
Python / Waitress (단일 프로세스)
        ├─ HTML + CSS + JavaScript 제공
        ├─ KDOne 인증 / 기기 조회 / 제어
        └─ SQLite 예약 실행 (2초 간격 점검)
```

HTML 파일만 Web Station 폴더에 복사하면 Python API와 예약은 동작하지 않습니다.
Python 서비스를 계속 실행하고 **같은 주소**로 화면과 `/api/`를 전달해야 합니다.
권장 방식은 Container Manager 프로젝트와 Web Station 포털을 연결하거나, HTTPS 역방향 프록시를 사용하는 것입니다.

공식 참고:

- [Web Station 웹 포털](https://kb.synology.com/index.php/en-us/DSM/help/WebStation/application_webserv_virtualhost?version=7)
- [Container Manager 컨테이너와 Web Station 포털](https://kb.synology.com/en-my/DSM/help/ContainerManager/docker_container?version=7)
- [Flask의 Waitress 운영 배포](https://flask.palletsprojects.com/en/stable/deploying/waitress/)

## 1. Windows에서 먼저 확인

현재 작업 환경에는 웹 실행 의존성을 설치했습니다. 새로 구성한다면 프로젝트 루트에서:

```powershell
.\.tools\uv\uv.exe pip install --python .venv/Scripts/python.exe -r requirements-web.txt
```

데모는 실제 서비스와 통신하지 않습니다. 샘플 기기와 별도의 `data/demo.sqlite3`만 사용합니다.

```powershell
.\.venv\Scripts\python.exe webapp.py --demo
```

브라우저에서 **http://127.0.0.1:8088** 접속. 종료는 터미널에서 `Ctrl+C`.

실제 기기 연결:

```powershell
# 아래 예시 대신 본인만 아는 숫자 6자리 PIN을 입력하세요.
$env:WEB_ADMIN_PIN = '012345'
$env:WEB_SECURE_COOKIE = '0'
.\.venv\Scripts\python.exe webapp.py
```

관리자 로그인 → 연결 설정 → 단지 검색 → KDOne 계정 → 월패드 인증 순서입니다.
웹 관리자 비밀번호와 NAVIEN 계정 비밀번호는 별개입니다. KDOne 비밀번호·토큰은 환경 파일에 넣지 않습니다.
로컬 기본 주소는 해당 PC에서만 열립니다. LAN 공개 시 인증과 HTTPS를 갖춘 NAS 구성을 사용하세요.

## 2. NAS에 소스 복사

예시 폴더: `/volume1/docker/kdone-home`. 다음 파일과 디렉터리를 복사합니다.

```text
compose.yaml              Dockerfile
requirements.txt          requirements-web.txt
home_service.py           schedules.py             webapp.py
config_store.py           config/*.example.json
templates/                static/                  upstream/KDOne/
.dockerignore             .env.example
```

Windows `.venv`, `.tools`, `data/demo.sqlite3`, 캡처, 테스트 산출물은 복사하지 않습니다.
Linux 가상환경은 Windows 가상환경과 호환되지 않습니다.

## 3. 관리자 설정과 비밀키

문서 상단의 외부 설정 절에 따라 `config/secrets.json`을 사용합니다.
이전 버전의 `.env`는 현재 Compose에서 읽지 않습니다.

무작위 비밀키 생성 예:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

생성된 키를 NAS의 `config/secrets.json` 안 `web_secret_key`에 넣습니다.
`config/settings.json`의 `secure_cookie=true`이면 **HTTPS**로 접속해야 로그인 쿠키가 동작합니다.
내부 HTTP로 초기 시험할 때만 `false`를 사용하고, HTTPS 전환 후 `true`로 변경해 컨테이너를 재시작하세요.

## 4. Container Manager / Docker 실행

Container Manager에서 프로젝트를 생성하고 프로젝트 폴더와 `compose.yaml`을 지정합니다.
SSH 명령으로 실행할 수도 있습니다. 설치된 버전에 따라 `docker compose` 대신 `docker-compose`를 사용합니다.

```sh
cd /volume1/docker/kdone-home
docker compose up -d --build
docker compose logs --tail=80
```

기본 바인딩은 NAS 내부의 `127.0.0.1:8088`입니다. 브라우저에서 `NAS_IP:8088`로 직접 접속하는 구성이 아닙니다.
Python은 컨테이너 안에서 8088 포트를 사용합니다. Web Station 포털을 만들 때 해당 컨테이너의 8088 서비스로 연결하세요.
포털 방식에 맞게 호스트 포트 바인딩 조정이 필요하면 NAS 내부에서만 접근하도록 유지하세요.

SQLite 예약 데이터는 Docker의 `kdone-data` 볼륨에 저장됩니다. 컨테이너 업데이트 시에도 유지됩니다.
**`docker compose down -v`는 예약 데이터를 삭제하므로 사용하지 마세요.**

## 5. HTTPS 진입점

단순한 구성은 DSM의 **제어판 → 로그인 포털 → 고급 → 역방향 프록시**에서 전용 호스트를 만드는 것입니다.

| 설정 | 예시 |
|---|---|
| 원본 | HTTPS / 직접 설정한 NAS 도메인 / 443 |
| 대상 | HTTP / 127.0.0.1 / 8088 |
| 인증서 | 해당 도메인의 유효한 인증서 |

Web Station을 사용한다면 컨테이너 웹 서비스를 등록하고 이름 기반 HTTPS 포털로 연결합니다.
기본 Web Station 페이지와 충돌하지 않도록 관리 앱용 호스트 이름을 구분하세요.
서브폴더(`/kdone`) 대신 **전용 호스트의 루트 경로(`/`)**로 연결하는 구성을 권장합니다.
화면과 `/api/*`, `/static/*` 모두 같은 Python 서비스로 전달되어야 합니다.
이 서비스는 다중 프록시 헤더를 신뢰하도록 설정하지 않았으며, 로그인 시도 제한은 프록시 주소 기준으로 합산될 수 있습니다.

외부 접속이 필요하지 않다면 포트 포워딩 없이 내부 DNS 또는 VPN에서만 접속하세요.

## 6. Docker를 사용할 수 없는 경우

NAS에서 Python **3.12 이상**을 별도로 확보한 뒤 NAS 안에서 가상환경을 새로 만듭니다.
시스템 기본 Python 버전을 바꾸지 마세요. 예시의 `python3.12`는 실제 설치 경로로 바꿉니다.

```sh
cd /volume1/apps/kdone-home
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-web.txt
export WEB_ADMIN_PIN='012345' # 본인의 숫자 6자리 PIN으로 변경
export WEB_SECRET_KEY='생성한-무작위-비밀키'
export WEB_SECURE_COOKIE=1
export KDONE_DATA_DIR=/volume1/apps/kdone-home/data
exec .venv/bin/python webapp.py --host 127.0.0.1 --port 8088
```

관리자 계정이 아닌 전용 계정에 코드 읽기와 `data` 쓰기 권한을 주고,
DSM 작업 스케줄러의 부팅 작업 또는 별도 서비스 관리자로 **하나의 프로세스만** 실행합니다.
부팅 작업은 상시 프로세스를 감시·재시작하지 않으므로 운영 시 프로세스 상태 확인이 필요합니다.
이 경로에서는 `.env`를 자동으로 읽지 않습니다. 환경 변수는 실행 서비스에 설정하세요.

## 예약 동작과 한계

- 일회성 켜기·끄기 예약을 제공합니다. 환기는 모드·풍량, 난방은 설정 온도를 함께 지정할 수 있습니다.
- `지금 시작하기 · 타이머`를 선택하면 예약 시각 대신 즉시 시작하고 동작 시간 뒤 끄는 타이머로 사용합니다.
- 켜기 예약에서 동작 시간을 지정하면 별도의 자동 끄기 예약이 함께 저장됩니다. 0분은 자동 끄기 없음입니다.
- 예약은 브라우저가 닫혀도 Python 프로세스가 실행 중이면 작동합니다. 표시와 입력 시각은 **한국 시간(UTC+9)**입니다.
- 취침 모드의 기기 자체 기본 480분 타이머는 이 프로그램에서 수정하지 않습니다.
  서버 예약 시간이 더 길더라도 기기가 자체적으로 먼저 멈출 수 있습니다.
- NAS 재시작·정전 후 외부 계정·인증값으로 재연결을 시도합니다. 인증값이 만료됐으면 관리 화면에서 **월패드 재인증**이 필요합니다. 토큰 자체는 복원하지 않습니다.
- 연결이 없으면 예약은 대기합니다. 자동 끄기는 연결 복구 후 늦게라도 실행합니다.
- 시간 구간이 이미 끝난 켜기 예약은 시작하지 않습니다. 자동 끄기 없는 예약은 시작 시각에서 5분 이상 지나면 생략합니다.
- 실행 실패는 실패 상태로 남기고 제어를 자동 재시도하지 않습니다. 서버가 응답하기 전에 연결이 끊겼다면 실제 상태를 조회하세요.
- 실행 도중 서버가 중단된 예약도 실패로 표시합니다. 중복 실행을 피하기 위해 재전송하지 않습니다.
- 시작 예약 취소는 아직 대기 중인 자동 끄기도 취소합니다. 시작 후 자동 끄기만 취소하면 현재 기기는 계속 켜져 있을 수 있습니다.
- 서버는 **단일 가정·단일 프로세스**용입니다. 여러 Worker나 컨테이너 복제본을 실행하면 안 됩니다.
- 가스·쿡탑은 차단만 지원하며 예약 대상에서 제외했습니다. 미검증 원격 켜기 시험 메뉴는 웹에 포함하지 않습니다.

## 점검

```powershell
.\.tools\uv\uv.exe pip install --python .venv/Scripts/python.exe -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests/test_web.py tests/test_config.py -q
node --check static/app.js
.\.venv\Scripts\python.exe console.py --self-test
```

Windows Edge가 설치되어 있고 8088 포트에 데모가 실행 중일 때 브라우저 점검:

```powershell
.\.venv\Scripts\python.exe tests/browser_smoke.py
```

예약 관리는 타이머와 자동화로 나뉩니다. 새 타이머는 자동화와 같은 동작·대기 편집기를 사용하며, 반복 없이 한 번 실행합니다. '1분 대기 → 환기 끄기 → 조명 켜기'처럼 일정 시간 후 여러 동작을 실행하거나, '환기 켜기 → 모드 변경 → 10분 대기 → 환기 끄기'처럼 즉시 실행과 대기를 조합할 수 있습니다. '지금 켜기 → 대기 → 끄기 구성' 버튼도 제공합니다. 대기 합계는 1~10080분이어야 합니다. 종료된 타이머는 타이머 목록에서 사라지고 실행 기록에 남으며, 실패한 타이머는 오류 확인·수정을 위해 남습니다.

타이머와 자동화 목록의 '수정' 버튼으로 이름, 동작·대기 순서를 바꿀 수 있습니다. 타이머는 저장 시 남은 동작을 교체하고 저장한 시점부터 새 순서의 처음부터 시작하므로 대기도 다시 계산됩니다. 이미 실행한 동작은 되돌리지 않습니다. 새 타이머의 '취소'는 남은 동작만 중단하며 기기에 끄기 명령을 자동 전송하지 않습니다. 기존 켜기·자동 끄기 타이머도 수정할 수 있으며, 수정하면 새 순차 타이머로 전환합니다. 자동화 수정은 다음 회차부터 적용하고 진행 중인 회차는 기존 설정으로 마칩니다. 일시정지 상태는 수정해도 유지됩니다. 시간 간격 자동화는 과거의 첫 실행 시각을 그대로 유지하면서 수정할 수 있습니다.

새 자동화는 매일, 선택한 요일, 또는 1~10080분 간격으로 순차 동작을 실행합니다. 시간 간격은 첫 실행 시각을 지정할 수 있습니다. 기기 동작 행에서는 조명·난방·환기의 대상 기기, 동작 항목과 값을 선택하고, 대기 행에서는 분 단위 시간을 지정합니다. 위아래 화살표로 순서를 바꿀 수 있습니다. 예를 들어 '환기 켜기 → 환기 전열 모드 → 풍량 3단 → 10분 대기 → 환기 끄기 → 식탁 조명 켜기'를 만들 수 있습니다. 켜기 하나, 끄기 하나만 등록하는 것도 가능합니다. 자동 모드의 풍량은 기기가 결정하므로 별도의 풍량 행을 추가하면 전열로 바뀔 수 있습니다.

자동 종료 명령은 자동으로 추가하지 않습니다. 끄기가 필요하면 명시적으로 끄기 행을 추가하세요. 마지막 행은 기기 동작이어야 하며 최대 50행입니다. 대기 합계는 반복 간격(매일·요일별은 24시간)보다 짧아야 합니다. 시각과 요일은 한국 시간 기준입니다. 같은 자동화의 이전 회차가 진행 중이면 다음 회차는 건너뜁니다. 대기는 앞 동작이 완료된 시점부터 계산하며 SQLite에 저장되어 서버 재시작 후에도 이어집니다. 동작 실패 시 해당 회차의 나머지 행은 실행하지 않고 오류를 기록합니다. 이 경우 뒤에 있던 끄기 행도 실행되지 않으므로 실제 기기 상태를 확인하세요.

기존 타이머의 '지금 종료'는 기기에 끄기 명령을 보냅니다. 새 타이머의 '취소'는 남은 순서만 중단합니다. 자동화 일시정지·삭제는 이후 시작을 막으며 이미 시작한 회차의 나머지 동작은 유지합니다. 새 순차 자동화는 시작이 5분 넘게 지연되면 해당 회차를 생략하며, 진행 중인 동작도 예정 시각에서 5분 넘게 지난 일반 동작은 생략하고 나머지를 중단합니다. 단, 대기 중인 전원 끄기는 연결 복구 후 실행합니다. 연결이 끊긴 동안에는 제어할 수 없습니다. 같은 기기의 서로 다른 일정이 겹치면 마지막 명령이 적용됩니다. 기존 켜기·자동 끄기 자동화와 일회성 예약, SQLite 데이터는 유지됩니다.

이 버전은 Python 백엔드와 Web Station 화면을 모두 업데이트해야 합니다. PC 기준 폴더는 `E:\Web_dev\KDOne_Home_Control`입니다. 아래 파일을 개별 복사해 덮어쓰세요. `templates/index.html`은 Web Station에 복사하지 마세요. Web Station용 HTML은 PHP API 경로가 반영된 별도 파일입니다.

| PC 원본 파일 | NAS 대상 파일 |
|---|---|
| `schedules.py` | `/volume1/docker/kdone-home/schedules.py` |
| `webapp.py` | `/volume1/docker/kdone-home/webapp.py` |
| `artifacts/webstation/index.html` | `/volume1/web/kdone/index.html` |
| `artifacts/webstation/api.php` | `/volume1/web/kdone/api.php` |
| `artifacts/webstation/static/app.js` | `/volume1/web/kdone/static/app.js` |
| `artifacts/webstation/static/app.css` | `/volume1/web/kdone/static/app.css` |

Python 파일 복사 후 NAS SSH에서 `cd /volume1/docker/kdone-home`을 실행하고 `sudo docker compose up -d --build`로 재빌드·재실행합니다. 화면 파일 복사 후 브라우저에서 Ctrl+F5로 새로고침합니다. 실제 `config/settings.json`, `config/secrets.json`과 데이터 볼륨은 유지하세요. 개발 시 `python package_webstation.py`를 실행하면 `artifacts/webstation/`에 복사용 최신 파일이 생성됩니다.

실제 기기에서는 관리자 로그인, 인증, 조회, 방 하나의 전원·온도, 환기, 예약을 순서대로 확인하세요.
개발 환경에서는 실제 계정에 접속하거나 가정 기기를 제어하지 않았습니다.

## 외부 API · 아이폰 단축어

설정과 요청 예시는 [EXTERNAL_API.md](EXTERNAL_API.md)에 정리되어 있습니다. 외부 API를 적용할 때는 PC의 `webapp.py`, `home_service.py`, `schedules.py`, `config_store.py`를 NAS `/volume1/docker/kdone-home/`에 덮어쓰고 재빌드합니다. Web Station에는 PC `artifacts/webstation/api.php`를 NAS `/volume1/web/kdone/api.php`로 복사합니다. 기존 설정 파일은 유지하고 `config/secrets.json`에 `api_keys`를 추가합니다. 키는 외부 설정에서 읽으며 소스·화면 파일에 넣지 않습니다. HTTP 웹 로그인 설정과 별개로 외부 API는 Web Station HTTPS로 호출합니다. 인터넷 접근 주소·인증서·공유기 설정은 NAS 환경에서 별도로 확인해야 합니다.
