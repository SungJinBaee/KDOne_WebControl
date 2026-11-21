# Web Station에서 화면 제공하기

현재 `Cannot set properties of null (setting 'textContent')`는 최신 JavaScript와 이전 HTML이
함께 제공될 때 발생할 수 있습니다. 특히 최신 `settings-auth-updated` 항목이 이전 HTML에 없습니다.
화면은 반드시 **같은 배포 ZIP의 index.html + static 전체**를 함께 업데이트하세요.

## 구성

```text
브라우저 → Web Station (index.html, static/, api.php)
                                   ↓ NAS 내부 HTTP
                         127.0.0.1:8088/api/*
                                   ↓
                         기존 Python 컨테이너
```

Python 컨테이너와 계정·예약 데이터는 그대로 사용합니다. 화면을 바꾸기 위한 이미지 빌드는 필요 없습니다.
`api.php`는 URL을 고정된 Python API로만 전달하고, 로그인 쿠키·CSRF 헤더를 유지합니다.
브라우저에는 계정·토큰을 보관하는 설정 파일을 복사하지 않습니다.

## 준비물

- Web Station과 **NAS 호스트에서 실행하는 PHP 8.x 프로필**
- PHP 프로필의 **curl 확장** 활성화
- Python 서버에서 NAS 내부 `http://127.0.0.1:8088/`에 200 응답
- 기존 `config/secrets.json`과 SQLite 볼륨 유지

컨테이너 안에서 따로 실행하는 PHP 프로필은 `127.0.0.1`이 NAS 호스트를 가리키지 않으므로
이 파일의 기본 구성과 다릅니다. 기본 패키지 PHP 프로필을 사용하세요.

## 순서

1. `artifacts/kdone-webstation.zip`을 NAS에 업로드합니다.
2. File Station의 `web` 공유 폴더 아래 `kdone` 폴더를 만들고 그 안에 압축을 풉니다.
   다음 네 파일만 공개 웹 폴더에 있어야 합니다.

   ```text
   index.html
   api.php
   static/app.js
   static/app.css
   ```

3. Web Station에서 이 폴더를 문서 루트로 하는 PHP 웹 서비스를 등록합니다.
   PHP 8.x 프로필을 연결하고 curl 확장을 활성화합니다.
   프록시가 오래 걸리는 조회를 기다릴 수 있도록 PHP/FPM 제한은 180초 이상으로 설정합니다.
4. Web Station 웹 포털을 해당 서비스에 연결합니다.
   HTTPS 2501은 기존 DSM 역방향 프록시가 이미 사용 중이므로 두 규칙을 같은 포트에 만들 수 없습니다.
   전환하기 전 기존 규칙을 비활성화하고 Web Station HTTPS 포털에 2501을 할당합니다.
   서비스·포털 등록 화면은 NAS에 설치된 Web Station 버전에 맞춰 진행합니다.
5. `https://NAS_IP:2501/api.php?path=session`에서 JSON 응답을 확인합니다.
   `csrf`, `authenticated`, `demo` 항목이 보이면 PHP→Python 연결이 정상입니다. 실제 csrf 값은 공유하지 마세요.
6. `https://NAS_IP:2501/`에서 관리자 로그인 후 상태 조회·조명 제어를 확인합니다.
   주소가 기존과 같으면 Ctrl+F5로 캐시를 갱신합니다. 로그인 쿠키는 기존 서버가 검증합니다.

HTTP 2500에서도 HTML을 제공할 수 있지만 현재 `secure_cookie=true` 설정이면 HTTP 로그인은 유지되지 않습니다.
실제 계정 제어는 HTTPS 2501을 사용하고 HTTP는 HTTPS 이동용으로 운영하는 것을 권장합니다.
이 문서의 화면 분리 작업은 해당 쿠키 보안 설정을 바꾸지 않습니다.

## 업데이트

PC에서 다음 명령으로 항상 같은 버전의 HTML·JS·CSS를 묶습니다.

```powershell
.\.venv\Scripts\python.exe package_webstation.py
```

생성된 ZIP 네 파일을 NAS `web/kdone`에 함께 교체합니다. JavaScript와 CSS 주소에 내용 기반 버전이
붙어 있어 이전 브라우저 캐시가 섞이는 것을 줄입니다. 실제 `config`는 웹 폴더에 복사하지 마세요.

## 현재 검증 범위

Python 자동 테스트와 기존 화면 브라우저 검증을 사용합니다. 개발 PC에는 PHP 실행기가 없어
PHP 브리지의 실제 실행과 Web Station 포털은 NAS에서 단계별 확인이 필요합니다.
기존 컨테이너 화면으로 복귀할 때는 역방향 프록시 2501의 대상을 다시 127.0.0.1:8088로 연결합니다.

참고: [PHP curl_setopt 공식 문서](https://www.php.net/manual/en/function.curl-setopt.php).
