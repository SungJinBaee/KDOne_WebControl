# 아이폰 단축어 · 외부 API

Web Station 주소는 `https://NAS도메인:2501/api.php?path=external/...`입니다.
아래 예시에서 도메인과 포트는 실제 외부 접속 주소로 바꾸세요. 사설 IP `192.168.x.x`는 집 밖에서 직접 접근할 수 없습니다. VPN 또는 외부 HTTPS 접속 구성이 필요합니다. 아이폰이 신뢰하는 인증서를 사용하세요. Python 8088 포트는 NAS 내부 전용으로 유지합니다.

## API 키 등록

NAS SSH에서 `/volume1/docker/kdone-home`으로 이동한 뒤 실행합니다.

```bash
sudo docker compose exec kdone python -c "import secrets; print(secrets.token_urlsafe(32))"
```

출력한 값을 NAS `/volume1/docker/kdone-home/config/secrets.json`의 최상위 `api_keys`에 등록합니다. 기존 계정과 관리자 설정을 유지하고 JSON 쉼표에 유의하세요. 키는 채팅이나 URL에 넣지 마세요.

```json
"api_keys": [
  {
    "name": "iPhone 단축어",
    "key": "여기에 생성한 키를 입력",
    "enabled": true,
    "scopes": ["read", "control", "timers", "automations"]
  }
]
```

키는 32자 이상이며 위 명령은 충분한 난수를 생성합니다. 실제 설정에서는 설명 문구 대신 생성한 값을 넣으세요. 권한은 필요한 항목만 지정할 수 있습니다. `enabled:false`, 키 삭제·교체는 저장 즉시 반영됩니다. 쿠키·관리자 로그인·CSRF 토큰은 외부 API에 필요하지 않습니다. 기존 웹 API의 인증 방식은 유지됩니다.

## 요청 목록

모든 요청 헤더에 `X-API-Key: 생성한 키`를 넣습니다. POST는 `Content-Type: application/json`과 JSON 본문도 필요합니다. 비어 있는 본문은 `{}`로 전송하세요. 키는 쿼리 문자열로 받지 않습니다. Web Station 외부 API는 HTTP 요청을 거부합니다.

| 방식 | path 값 | 권한 | 내용 |
|---|---|---|---|
| GET | `external/state` | read | 연결 상태와 기기 식별자·상태 조회 |
| GET | `external/schedules` | read | 타이머·자동화 목록과 ID 조회 |
| POST | `external/control` | control | 기기 동작 1개 전송 |
| POST | `external/timers` | timers | 한 번 실행할 동작·대기 순서 등록 |
| POST | `external/automations/자동화ID/run` | automations | 저장된 자동화를 한 번 실행 |

자동화 실행은 일시정지 중에도 가능하며 활성화 상태와 다음 반복 시각을 바꾸지 않습니다. 이미 대기·실행 중이면 409로 거절합니다. 200 응답은 실행 대기열 등록을 뜻하며, 실제 결과는 실행 기록을 확인하세요. 통신 오류 후 POST를 무조건 재전송하지 말고 상태·기록을 확인하세요. 단일 기기 제어의 `warning`이 있으면 전송 후 상태 재조회가 실패한 것입니다.

## 단축어: 식탁 조명 켜기

1. 'URL' 동작에 `https://NAS도메인:2501/api.php?path=external/control`을 입력합니다.
2. 'URL 콘텐츠 가져오기'를 추가하고 방식을 POST로 설정합니다.
3. 헤더에 `X-API-Key`와 생성한 키, `Content-Type`과 `application/json`을 지정합니다.
4. 요청 본문을 JSON으로 설정하고 `action` 사전 안에 아래 문자열 항목을 넣습니다.

```json
{
  "action": {
    "family": "lights",
    "group": "1",
    "sub": "5",
    "field": "OnOffStatus",
    "value": "On"
  }
}
```

끄기는 `value`를 `Off`로 바꾸면 됩니다. 각 집의 기기 식별자는 `external/state`에서 확인합니다.

## 환기 및 난방

환기: family=`ventilation`, group=`0`, sub=`1`. field/value는 `PowerStatus`=`On`/`Off`, `Mode`=`Normal`/`Sleep`/`Pleasant`/`Auto`, `WindPower`=`1`/`2`/`3`입니다. 켜기 → 모드 → 풍량 순서로 실행하고, Auto에서는 풍량 명령을 보내지 마세요. 난방은 family=`heating`, field=`Heating`, `Mode`, `SettingTemp`를 사용합니다. 대상·범위는 조회 응답을 따릅니다.

## 단축어: 저장한 자동화 실행

`external/schedules`를 GET으로 조회해 원하는 자동화 ID를 확인합니다. 같은 헤더로 다음 URL을 POST 호출하고 JSON 본문은 `{}`로 보냅니다.

```text
https://NAS도메인:2501/api.php?path=external/automations/자동화ID/run
```

## 10분 후 환기 끄기와 식탁 조명 켜기

`external/timers`에 다음 JSON을 POST합니다.

```json
{
  "label": "10분 후 환기 종료",
  "sequence": [
    {"type": "wait", "minutes": 10},
    {"type": "action", "action": {"family": "ventilation", "group": "0", "sub": "1", "field": "PowerStatus", "value": "Off"}},
    {"type": "action", "action": {"family": "lights", "group": "1", "sub": "5", "field": "OnOffStatus", "value": "On"}}
  ]
}
```

이후 웹페이지에서 타이머 수정·취소가 가능합니다. 동작·대기의 제한과 실패 처리 방식은 웹 타이머와 같습니다. 401은 키 오류·비활성, 403은 권한 부족 또는 HTTP 호출, 400은 본문·값 오류, 404는 없는 자동화, 409는 실행 중이거나 기기 연결 필요 상태입니다.
