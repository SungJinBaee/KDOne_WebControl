# KDOne Home Control

경동원 NAVIEN 홈네트워크 기기를 Python 콘솔과 웹 대시보드에서 관리하는 프로젝트입니다.

PC에서 테스트하거나 Synology NAS의 Container Manager와 Web Station으로 운영할 수 있습니다.

## 주요 기능

동일 항목·날짜·후보 값의 중복 검토는 한 번의 결정으로 함께 처리합니다. 서로 다른 후보 값은 별도로 남깁니다. 기존 값 유지를 선택한 동일 후보는 확정값 버전이 바뀌기 전까지 재수집돼도 다시 검토 대기에 올리지 않습니다. 원본 관측값은 계속 보존합니다. 검토 사유를 입력해야 처리할 수 있으며 처리 결과·오류는 검토 목록 위에 표시합니다.

조회·집계 시점의 한국 날짜 기준 오늘과 어제 데이터는 20% 변동 검토에서 제외하고 정상 수집값을 바로 반영합니다(0→양수 포함). 그보다 과거인 기록에는 20% 기준을 적용합니다. 오늘·어제라도 직접 수정값 보호, 단위 변경, 누락·잘못된 값 검토는 유지합니다. 이전 버전에서 오늘·어제의 비율 변화만으로 보류된 항목은 다음 정상 수집 시 제외 처리하고 이력은 보존합니다.

전년 비교 표는 `전년 → 선택연도 → 증감` 순서로 표시합니다. 증감은 선택연도에서 전년을 뺀 사용량이며, 양쪽 값과 단위가 모두 확인될 때만 계산합니다. 일별 표는 월말까지, 월별 표는 12월까지 표시하며 올해 아직 없는 기록은 `—`입니다. 이전 API가 전년 조회를 오늘 날짜에서 잘라 반환하면 서버 업데이트 안내가 표시됩니다.

사용량 메뉴에서 전기·수도·난방·온수·가스를 선택하고 일별(조회 월) 또는 월별(조회 연도) 기록을 확인할 수 있습니다. 전년 비교를 선택하면 선택한 달 전체 또는 1~12월 기록을 나란히 표시합니다. 2023년부터 수집한 일별 확정값을 SQLite에 보존하고 월별은 이 값의 합계로 계산합니다. 누락 기록은 0이 아닌 `—`, 일부 일자만 있는 월은 부분 집계로 표시합니다. 요금 계산 기능은 아닙니다.

매일 한국 시간 03:00에 최근 완료된 3일, 웹 조회 시 선택 항목의 당일과 최근 2일을 재수집합니다. 기존 값 대비 절대 변화율이 20% 이상이면 검토 대기로 남기고 기존 확정값을 유지합니다. 0에서 양수로 바뀌거나 단위가 달라지거나 응답이 누락·잘못된 경우도 검토 대상으로 남깁니다. 20% 미만 변화는 기존 검토가 없을 때 자동 반영합니다. 검토 중인 날짜는 결정 전까지 확정값을 고정합니다.

사용량의 `수집 상태 · 검토 · 값 수정`에서 원본과 변경 이력을 보고 새 값 채택·기존 값 유지 또는 일별 값을 직접 수정할 수 있습니다. 직접 수정한 값은 자동 수집으로 덮어쓰지 않습니다. 수정·채택에는 사유가 필요하고 동시 변경이 있으면 재확인을 요구합니다. 원본 측정 필드와 결정·수정 이력은 삭제하지 않으며 전체 CSV ZIP 다운로드를 지원합니다. 자세한 배포 절차는 [NAS_SETUP.md](NAS_SETUP.md)를 참고하세요.

앱 4.14.04의 `/used/list` 요청·응답 구조를 분석해 구현했습니다. 자동 테스트와 데모 브라우저 검증을 완료했으며, 운영 단지의 실제 응답은 NAS 배포 후 앱과 비교 확인해야 합니다. 사용량 조회는 현재 인증 토큰을 사용하며 별도의 로그인·월패드 인증 요청이나 목표 사용량 저장을 실행하지 않습니다.

| 기능 | 지원 내용 |
|---|---|
| 조명 | 조회, 켜기·끄기, 표시 이름 변경 |
| 난방 | 방별 전원, 온도, 난방·외출 모드 |
| 환기 | 전원, 일반·취침·전열·자동 모드, 풍량 1~3단 |
| 주방 | 가스밸브와 쿡탑 개별 차단 |
| 엘리베이터 | 호출 |
| 타이머 | 대기 후 여러 동작을 한 번 실행, 수정·취소 |
| 자동화 | 매일·요일별·시간 간격 반복, 동작·대기 순서 편집 |
| 외부 API | 키 인증, 조회·제어·타이머 등록·자동화 즉시 실행 |

자동 환기 모드는 풍량도 자동으로 결정합니다. 취침에서 전열·자동으로 전환할 때는 일반 모드를 거칩니다. 가스밸브와 쿡탑의 원격 켜기는 사용자 환경에서 동작하지 않아 웹 제어에서 제공하지 않습니다.

## 빠른 시작

Python 3.12와 Windows PowerShell 기준입니다.

### 설치

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
```

### 웹 데모

```powershell
.\.venv\Scripts\python.exe webapp.py --demo
```

브라우저에서 [http://127.0.0.1:8088](http://127.0.0.1:8088)에 접속합니다. 데모는 실제 서버나 기기에 명령을 보내지 않습니다.

### 콘솔 테스트

```powershell
# 실제 서버에 연결하지 않는 자체 점검
.\.venv\Scripts\python.exe console.py --self-test

# 실제 서버에서 단지 목록 조회
.\.venv\Scripts\python.exe console.py --complexes

# 로그인과 월패드 인증 후 기기 조회·제어
.\.venv\Scripts\python.exe console.py
```

## 실제 연결과 설정

예제 파일을 복사해 설정 파일을 만듭니다. 기존 설정 파일이 있으면 덮어쓰지 마세요.

```powershell
Copy-Item config/settings.example.json config/settings.json
Copy-Item config/secrets.example.json config/secrets.json
```

| 설정 파일 | 내용 |
|---|---|
| `config/settings.json` | 집·단지 이름, 기기 표시 이름, 인증 갱신 주기, 쿠키 설정 |
| `config/secrets.json` | 관리자 비밀번호, 웹 세션 비밀키, 계정·인증정보, API 키 |

실제 웹 실행 전 `web_admin_pin`을 숫자 6자리 문자열로 설정하고, `web_secret_key`에 무작위 비밀키를 넣습니다. 로그인 키패드는 화면을 열거나 인증에 실패할 때마다 숫자를 재배치합니다. 자세한 설정은 [NAS 설치 안내](NAS_SETUP.md)를 참고하세요.

```powershell
.\.venv\Scripts\python.exe webapp.py
```

웹 관리자 로그인 후 월패드 인증으로 연결합니다. 계정과 인증정보는 외부 설정 파일에, 접속 토큰은 서버 메모리에 보관합니다. 기본 15분마다 인증 갱신을 시도하지만 서버 측 인증 취소나 만료를 보장해서 막을 수는 없습니다.

`secure_cookie: true`이면 HTTPS에서만 웹 로그인이 유지됩니다. 내부망 HTTP 로그인을 함께 사용하려면 `false`로 변경하고 서버를 재시작합니다. 외부 API는 Web Station의 HTTPS 주소로 호출합니다.

## 타이머와 자동화

기기 동작과 대기를 위에서 아래로 실행합니다. 서로 다른 기기를 하나의 순서에 넣을 수 있습니다.

```text
환기 켜기 → 전열 모드 → 풍량 3단 → 10분 대기 → 환기 끄기 → 식탁 조명 켜기
```

- **타이머:** 반복 없이 한 번 실행합니다. 대기를 먼저 넣으면 일정 시간 뒤 시작합니다. 수정하면 저장한 시점부터 새 순서를 시작합니다.
- **자동화:** 매일, 선택한 요일, 지정한 시간 간격으로 반복합니다. 첫 실행 시각을 지정할 수 있으며, 수정은 다음 회차부터 적용합니다.
- **실행 기록:** 종료된 타이머는 목록에서 사라지고 기록에 남습니다. 동작 실패 시 해당 회차의 나머지 행은 중단됩니다.

일정은 SQLite에 보관하며 브라우저를 닫아도 실행합니다. NAS 중지나 통신 장애 중에는 제어할 수 없습니다. 끄기가 필요하면 끄기 행을 직접 넣어 주세요.

## NAS 배포와 외부 API

배포 확인 환경은 DS216+II, DSM 7.4, Container Manager, Web Station PHP 8.2입니다.

```text
브라우저·아이폰 단축어
        ↓
Web Station: HTML / CSS / JavaScript / api.php
        ↓
Python API: NAS 내부 127.0.0.1:8088
        ↓
NAVIEN 홈네트워크 서버
```

| 안내 문서 | 내용 |
|---|---|
| [NAS_SETUP.md](NAS_SETUP.md) | 설치, 외부 설정, 재빌드·재시작 |
| [WEB_STATION_SETUP.md](WEB_STATION_SETUP.md) | Web Station과 PHP API 연결 |
| [EXTERNAL_API.md](EXTERNAL_API.md) | API 키, 외부 호출, 아이폰 단축어 |

Web Station용 복사 파일을 생성합니다.

```powershell
.\.venv\Scripts\python.exe package_webstation.py
```

생성된 `artifacts/webstation/`의 파일을 NAS 웹 폴더에 복사합니다. `templates/index.html`은 Python 서버용이므로 Web Station에 직접 복사하지 마세요.

실제 계정 설정, 비밀번호, 인증정보, API 키와 실행 데이터는 Git에서 제외합니다. 업데이트 시 NAS의 기존 설정과 데이터 볼륨을 유지하세요.

## 프로젝트 구조

```text
console.py                 콘솔 실행기
webapp.py                  Flask API와 Waitress 서버
home_service.py            기기 연결·인증·제어
config_store.py            외부 설정 관리
schedules.py               타이머·자동화 저장과 실행
templates/                 Python 서버용 HTML
static/                    JavaScript와 CSS
webstation/api.php         Web Station과 Python API 연결
config/*.example.json      설정 예제
tests/                     데모·모의·브라우저 테스트
upstream/KDOne/            원본 라이브러리 소스와 라이선스
compose.yaml               NAS 컨테이너 구성
```

## 개발 검증

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
node --check static/app.js
```

Edge가 설치된 환경에서 웹 데모를 실행한 뒤 브라우저 테스트를 진행합니다.

```powershell
.\.venv\Scripts\python.exe tests/browser_smoke.py
```

자동 테스트는 실제 가정 기기를 제어하지 않습니다.

## 원본 라이브러리

- 원본: [soulee-dev/KDOne](https://github.com/soulee-dev/KDOne)
- 기반 커밋: `85a9d354b5e97dc0d9d668e0d0624f8f1d795d9e`
- 원본 라이선스: [MIT](upstream/KDOne/LICENSE)

원본 소스는 `upstream/KDOne/`에 포함되어 있습니다.
