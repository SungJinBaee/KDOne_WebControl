"""KDOne console test runner. Credentials and tokens stay in memory."""
import argparse
import contextlib
import getpass
import io
import json
import sys
import time
from decimal import Decimal, InvalidOperation
from unittest.mock import patch

import truststore

truststore.inject_into_ssl()

import requests
from KDOne.api import KDOneAPI
from KDOne.models.device import DeviceType, StatusType
from KDOne.utils.encryption import encrypt_payload, decrypt_payload, random_key

VENTILATION_MODES = {"Normal": "일반", "Sleep": "취침", "Pleasant": "전열", "Auto": "자동"}


class ConsoleRequestError(Exception):
    """Request diagnostics without credentials, headers, or response bodies."""


def describe_error_response(response, secrets=()):
    """Inspect error envelopes without exposing response bodies or auth values."""
    payload = None
    form = "JSON 아님"
    try:
        payload = response.json()
        form = "JSON"
    except ValueError:
        key = response.headers.get("x-message-id")
        if key:
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    payload = json.loads(decrypt_payload(key, response.text))
                form = "암호화 JSON"
            except Exception:
                form = "암호화 응답 해석 실패"
    code = payload.get("Error_Cd") if isinstance(payload, dict) else None
    # Error codes are numeric in the known protocol. Never echo arbitrary text.
    safe_code = str(code) if isinstance(code, (str, int)) else ""
    if not (safe_code.isascii() and safe_code.isdigit() and len(safe_code) <= 10):
        safe_code = "없음/지원하지 않는 형식"
    description = payload.get("Error_Nm") if isinstance(payload, dict) else None
    if isinstance(description, str):
        for secret in sorted((s for s in secrets if isinstance(s, str) and s),
                             key=len, reverse=True):
            description = description.replace(secret, "[숨김]")
        description = " ".join(description.split())
        description = "".join(c for c in description if c.isprintable())[:200]
    else:
        description = "없음"
    return f"응답 형식={form}, Error_Cd={safe_code}, Error_Nm={description}"


def quiet_call(function, *args, _diagnostics=False, **kwargs):
    # The upstream crypto helpers print encryption keys even with verbose=False.
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return function(*args, **kwargs)
    except requests.RequestException as error:
        stage = {
            "get_complexes": "단지 목록 조회",
            "login": "로그인/인증번호 요청",
            "_get_certification_code": "월패드 인증번호 요청",
            "certify": "월패드 인증번호 검증",
            "get_token": "로그인 토큰 발급",
            "get_devices": "조명 조회",
            "get_ventilation": "환기 조회",
            "get_heating": "난방 조회",
            "get_hybrid": "가스·전기레인지 조회",
            "close_hybrid_valve": "하이브리드 차단 요청",
            "turn_off_induction": "쿡탑 차단 시험",
            "test_hybrid_enable": "가스·쿡탑 켜기 시험",
            "control_heating_power": "난방 전원 제어",
            "control_heating_temperature": "난방 온도 설정",
            "control_heating_away": "난방 외출 모드 제어",
            "control_ventilation_power": "환기 전원 제어",
            "control_ventilation_setting": "환기 설정 제어",
            "control_device": "조명 제어",
            "call_elevator": "엘리베이터 호출",
        }.get(function.__name__, "API 요청")
        if isinstance(error, requests.exceptions.SSLError):
            detail = "TLS 인증서 검증 실패"
        elif isinstance(error, requests.Timeout):
            detail = "요청 시간 초과"
        elif error.response is not None:
            detail = f"HTTP {error.response.status_code}"
        elif isinstance(error, requests.ConnectionError):
            detail = "서버 연결 실패 (DNS/TCP/연결 종료 등)"
        else:
            detail = "HTTP 요청 실패"
        if _diagnostics and error.response is not None:
            owner = getattr(function, "__self__", None)
            secrets = [getattr(owner, field, None) for field in (
                "username", "password", "app_certify", "access_token", "refresh_token")]
            detail += f" ({describe_error_response(error.response, secrets)})"
        raise ConsoleRequestError(f"{stage} 실패: {detail}") from None


def self_test():
    message = '{"message": "한글 테스트"}'
    key = random_key()
    encrypted = quiet_call(encrypt_payload, key, message)
    assert quiet_call(decrypt_payload, key, encrypted) == message
    api = KDOneAPI("test-user", "test-password", verbose=False)
    with patch.object(api, "_send_request") as send:
        api.login("test-complex")
        api.certify("123456")
        send.return_value = {"Resources": [{"Oauth": {
            "Access_Token": "test-access", "Refresh_Token": "test-refresh"}}]}
        api.get_token()
        send.return_value = {"Resources": [{"Device_Type": "1401",
            "Group_Id": "1", "Sub_Id": "1", "Device_Remark": "거실",
            "Status_Type": "OnOffStatus", "Status": "Off"}]}
        devices = api.get_devices(DeviceType.LIGHT)
        assert devices[0].remark == "거실"
        api.control_device(devices[0], "On")
        assert send.call_args.args[0] == "/device/control"
        assert send.call_args.args[1]["Status"] == "On"
        assert send.call_args.args[1]["Access_Token"] == "test-access"
    print("PASS: 암복호화, 모의 인증/토큰/기기 조회/조명 제어 (실제 네트워크 사용 없음)")


def show_devices(api):
    devices = quiet_call(api.get_devices, DeviceType.LIGHT)
    for index, device in enumerate(devices, 1):
        print(f"{index}. {device.remark} | {device.status_type.value}={device.status} "
              f"| group={device.group_id}, sub={device.sub_id}")
    if not devices:
        print("조회된 조명이 없습니다.")
    return devices


def get_ventilation(api):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    response = api._send_request("/device/list", {
        "Complex": api.complex_id,
        "Device_Type": "140B",
        "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token,
    })
    resources = response.get("Resources", [])
    if not isinstance(resources, list) or any(not isinstance(row, dict) for row in resources):
        raise ValueError("환기 목록 응답 형식이 다릅니다.")
    return resources


def show_ventilation(api):
    # Keep unknown status names as strings rather than using the light-only enums.
    devices = quiet_call(get_ventilation, api)
    for index, row in enumerate(devices, 1):
        print(f"{index}. {row.get('Device_Remark', '환기')} | "
              f"{row.get('Status_Type', '?')}={row.get('Status', '?')} | "
              f"group={row.get('Group_Id', '?')}, sub={row.get('Sub_Id', '?')}")
    if not devices:
        print("조회된 환기 기기가 없습니다.")
    return devices


def get_heating(api):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    response = api._send_request("/device/list", {
        "Complex": api.complex_id, "Device_Type": "140C",
        "Access_Token": api.access_token, "Refresh_Token": api.refresh_token,
    })
    rows = response.get("Resources", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("난방 목록 응답 형식이 다릅니다.")
    return rows


def show_heating(api):
    rows = quiet_call(get_heating, api)
    for index, row in enumerate(rows, 1):
        print(f"{index}. {row.get('Device_Remark', '난방')} | "
              f"{row.get('Status_Type', '?')}={row.get('Status', '?')} | "
              f"group={row.get('Group_Id', '?')}, sub={row.get('Sub_Id', '?')}")
    if not rows:
        print("조회된 난방 기기가 없습니다.")
    return rows


def get_hybrid(api):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    response = api._send_request("/device/list", {
        "Complex": api.complex_id, "Device_Type": "1402",
        "Access_Token": api.access_token, "Refresh_Token": api.refresh_token,
    })
    rows = response.get("Resources", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("가스·전기레인지 목록 응답 형식이 다릅니다.")
    return rows


def show_hybrid(api):
    rows = quiet_call(get_hybrid, api)
    for index, row in enumerate(rows, 1):
        print(f"{index}. {row.get('Device_Remark', '하이브리드')} | "
              f"{row.get('Status_Type', '?')}={row.get('Status', '?')} | "
              f"group={row.get('Group_Id', '?')}, sub={row.get('Sub_Id', '?')}")
    if not rows:
        print("조회된 가스·전기레인지 기기가 없습니다.")
    return rows


def close_hybrid_valve(api, device):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    if device.get("Device_Type") != "1402" or device.get("Status_Type") != "GasValveStatus":
        raise ValueError("가스밸브 차단 항목만 선택할 수 있습니다.")
    # Only the captured Close command is supported; never open or energize.
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "1402",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "GasValveStatus", "Status": "Close",
    })


def turn_off_induction(api, device):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    if device.get("Device_Type") != "1402" or device.get("Status_Type") != "InductionStatus":
        raise ValueError("쿡탑 전원 항목만 선택할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "1402",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "InductionStatus", "Status": "Off",
    })


def induction_off_menu(api):
    rows = quiet_call(get_hybrid, api)
    devices = [row for row in rows if row.get("Device_Type") == "1402"
               and row.get("Status_Type") == "InductionStatus"]
    if not devices:
        print("조회된 쿡탑 전원 항목이 없습니다.")
        return
    for index, device in enumerate(devices, 1):
        print(f"{index}. {device.get('Device_Remark', '쿡탑')} | "
              f"InductionStatus={device.get('Status', '?')} | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    index = int(input("쿡탑 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("대상 번호 범위를 확인하세요.")
        return
    device = devices[index - 1]
    print("InductionStatus=Off 명령으로 쿡탑 전원을 차단합니다.")
    if input(f"group={device['Group_Id']}, sub={device['Sub_Id']}: 실제 쿡탑 차단 시험? (yes): ").strip() != "yes":
        return
    quiet_call(turn_off_induction, api, device, _diagnostics=True)
    print("쿡탑 차단 시험 요청 성공. 실제 상태를 다시 조회합니다.")
    try:
        show_hybrid(api)
    except (ConsoleRequestError, ValueError, KeyError, TypeError):
        print("요청은 전송했으나 재조회에 실패했습니다. 메뉴 13과 실제 기기에서 확인하세요.")
        return
    print("InductionStatus=Off와 실제 쿡탑 차단 여부를 확인하세요.")


def test_hybrid_enable(api, device):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    field = device.get("Status_Type")
    if device.get("Device_Type") != "1402" or field not in ("GasValveStatus", "InductionStatus"):
        raise ValueError("가스밸브 또는 쿡탑 전원 항목만 선택할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "1402",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": field, "Status": "Open" if field == "GasValveStatus" else "On",
    })


def hybrid_enable_test_menu(api, field):
    rows = quiet_call(get_hybrid, api)
    devices = [row for row in rows if row.get("Device_Type") == "1402"
               and row.get("Status_Type") == field]
    if not devices:
        print("시험할 수 있는 기기 항목이 없습니다.")
        return
    for index, device in enumerate(devices, 1):
        print(f"{index}. {device.get('Device_Remark', field)} | {field}={device.get('Status', '?')} | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    index = int(input("시험 대상 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("대상 번호 범위를 확인하세요.")
        return
    device = devices[index - 1]
    if field == "GasValveStatus":
        print("검증되지 않은 GasValveStatus=Open 요청입니다. 현장에서 모든 가스기기의 밸브가 잠겼고")
        print("가스 냄새·누출 징후가 없음을 확인한 경우에만 진행하세요. 기기의 거부 응답은 우회하지 않습니다.")
        confirmation = "GAS_OPEN"
    else:
        print("검증되지 않은 InductionStatus=On 요청입니다. 현장에서 화구 조작부가 꺼져 있고")
        print("상판에 가연물·조리도구가 없는지 확인하세요. 전원 복구로 가열이 재개될 수 있습니다.")
        confirmation = "COOKTOP_ON"
    print(f"대상 group={device['Group_Id']}, sub={device['Sub_Id']}")
    if input(f"현장 확인 후 시험 전송하려면 {confirmation} 입력 (그 외: 취소): ").strip() != confirmation:
        print("시험을 취소했습니다.")
        return
    quiet_call(test_hybrid_enable, api, device, _diagnostics=True)
    print("시험 요청 성공. 실제 상태를 다시 조회합니다.")
    try:
        show_hybrid(api)
    except (ConsoleRequestError, ValueError, KeyError, TypeError):
        print("요청은 전송했으나 재조회에 실패했습니다. 현장에서 실제 상태를 확인하세요.")
        return
    print("요청 성공과 실제 동작은 다를 수 있습니다. 현장에서 상태를 확인하세요.")


def hybrid_shutdown_menu(api):
    rows = quiet_call(get_hybrid, api)
    devices = [row for row in rows if row.get("Device_Type") == "1402"
               and row.get("Status_Type") == "GasValveStatus"]
    if not devices:
        print("선택할 수 있는 가스밸브 차단 항목이 없습니다.")
        return
    for index, device in enumerate(devices, 1):
        print(f"{index}. {device.get('Device_Remark', '가스밸브')} | "
              f"GasValveStatus={device.get('Status', '?')} | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    index = int(input("차단 대상 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("대상 번호 범위를 확인하세요.")
        return
    device = devices[index - 1]
    print("가스밸브만 차단합니다. 쿡탑은 별도 메뉴 15에서 시험할 수 있습니다.")
    if input(f"group={device['Group_Id']}, sub={device['Sub_Id']}: 실제 차단 요청? (yes): ").strip() != "yes":
        return
    quiet_call(close_hybrid_valve, api, device)
    print("차단 요청 성공. 실제 상태를 다시 조회합니다.")
    try:
        show_hybrid(api)
    except (ConsoleRequestError, ValueError, KeyError, TypeError):
        print("차단 요청은 전송했으나 재조회에 실패했습니다. 메뉴 13과 실제 기기에서 확인하세요.")
        return
    print("GasValveStatus=Close와 실제 가스밸브 차단 여부를 확인하세요.")


def control_heating_power(api, device, status):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    if (device.get("Device_Type") != "140C"
            or device.get("Status_Type") != "Heating"
            or status not in ("On", "Off")):
        raise ValueError("난방 전원 항목과 On/Off만 제어할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "140C",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "Heating", "Status": status,
    })


def control_heating_temperature(api, device, temperature, rows):
    if (not api.access_token or device.get("Device_Type") != "140C"
            or device.get("Status_Type") != "SettingTemp"):
        raise ValueError("로그인한 난방 설정 온도 항목만 제어할 수 있습니다.")
    states = {row.get("Status_Type"): row.get("Status") for row in rows
              if row.get("Device_Type") == "140C"
              and str(row.get("Group_Id")) == str(device.get("Group_Id"))
              and str(row.get("Sub_Id")) == str(device.get("Sub_Id"))}
    try:
        value = Decimal(str(temperature))
        minimum = Decimal(str(states["SettingMin"]))
        maximum = Decimal(str(states["SettingMax"]))
        if not all(number.is_finite() for number in (value, minimum, maximum)):
            raise ValueError("유효한 온도를 입력하세요.")
        if not minimum <= value <= maximum:
            raise ValueError(f"설정 온도는 {minimum}~{maximum}℃ 범위여야 합니다.")
        # Fractional control has not been verified. Float=N devices use integers.
        if value != value.to_integral_value():
            raise ValueError("현재는 정수 온도만 지원합니다.")
    except (InvalidOperation, KeyError):
        raise ValueError("온도 또는 기기의 설정 범위를 확인하세요.") from None
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "140C",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "SettingTemp", "Status": f"{value:.1f}",
    })


def heating_temperature_menu(api):
    rows = quiet_call(get_heating, api)
    devices = [row for row in rows if row.get("Device_Type") == "140C"
               and row.get("Status_Type") == "SettingTemp"]
    if not devices:
        print("설정할 수 있는 난방 온도 항목이 없습니다.")
        return
    for index, device in enumerate(devices, 1):
        states = {row.get("Status_Type"): row.get("Status") for row in rows
                  if row.get("Device_Type") == "140C"
                  and str(row.get("Group_Id")) == str(device.get("Group_Id"))
                  and str(row.get("Sub_Id")) == str(device.get("Sub_Id"))}
        print(f"{index}. {device.get('Device_Remark', '난방')} | "
              f"현재={states.get('CurrentTemp', '?')}℃ / 설정={device.get('Status', '?')}℃ | "
              f"범위={states.get('SettingMin', '?')}~{states.get('SettingMax', '?')}℃ | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    index = int(input("난방 방 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("방 번호 범위를 확인하세요.")
        return
    device = devices[index - 1]
    temperature = input("설정 온도 ℃ (정수, 취소: Enter): ").strip()
    if not temperature:
        return
    if input(f"{device.get('Device_Remark', '난방')} "
             f"(group={device['Group_Id']}, sub={device['Sub_Id']}): "
             f"{temperature}℃ 설정? (yes): ").strip() != "yes":
        return
    try:
        quiet_call(control_heating_temperature, api, device, temperature, rows)
    except ValueError as error:
        print(str(error))
        return
    print("난방 온도 설정 요청 성공. 메뉴 9에서 상태와 실제 기기를 확인하세요.")


def control_heating_away(api, device, status):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    if (device.get("Device_Type") != "140C" or device.get("Status_Type") != "Mode"
            or status not in ("On", "Off")):
        raise ValueError("난방 외출 모드 항목과 On/Off만 제어할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "140C",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "Mode", "Status": status,
    })


def heating_away_menu(api):
    rows = quiet_call(get_heating, api)
    devices = [row for row in rows if row.get("Device_Type") == "140C"
               and row.get("Status_Type") == "Mode"]
    if not devices:
        print("설정할 수 있는 난방 외출 모드 항목이 없습니다.")
        return
    labels = {"On": "외출 설정", "Off": "외출 해제"}
    for index, device in enumerate(devices, 1):
        current = device.get('Status', '?')
        print(f"{index}. {device.get('Device_Remark', '난방')} | "
              f"{labels.get(current, current)} | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    index = int(input("난방 방 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("방 번호 범위를 확인하세요.")
        return
    choice = input("1: 외출 설정 / 2: 외출 해제 / 0: 취소 > ").strip()
    if choice == "0":
        return
    if choice not in ("1", "2"):
        print("1 또는 2를 선택하세요.")
        return
    status = "On" if choice == "1" else "Off"
    device = devices[index - 1]
    if input(f"{device.get('Device_Remark', '난방')} "
             f"(group={device['Group_Id']}, sub={device['Sub_Id']}): "
             f"{labels[status]}? (yes): ").strip() != "yes":
        return
    quiet_call(control_heating_away, api, device, status)
    print("난방 외출 모드 요청 성공. 메뉴 9에서 Mode와 Heating 상태를 확인하세요.")


def heating_power_menu(api):
    rows = quiet_call(get_heating, api)
    devices = [row for row in rows if row.get("Device_Type") == "140C"
               and row.get("Status_Type") == "Heating"]
    if not devices:
        print("제어할 수 있는 난방 전원 항목이 없습니다.")
        return
    for index, device in enumerate(devices, 1):
        states = {row.get("Status_Type"): row.get("Status") for row in rows
                  if row.get("Device_Type") == "140C"
                  and str(row.get("Group_Id")) == str(device.get("Group_Id"))
                  and str(row.get("Sub_Id")) == str(device.get("Sub_Id"))}
        print(f"{index}. {device.get('Device_Remark', '난방')} | "
              f"전원={device.get('Status', '?')} | "
              f"현재={states.get('CurrentTemp', '?')}℃ / 설정={states.get('SettingTemp', '?')}℃ | "
              f"group={device.get('Group_Id', '?')}, sub={device.get('Sub_Id', '?')}")
    try:
        index = int(input("난방 방 번호 (취소: 0): "))
    except ValueError:
        print("목록의 번호를 입력하세요.")
        return
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("방 번호 범위를 확인하세요.")
        return
    status = input("On / Off: ").strip().capitalize()
    if status not in ("On", "Off"):
        print("On 또는 Off를 입력하세요.")
        return
    device = devices[index - 1]
    if input(f"{device.get('Device_Remark', '난방')} "
             f"(group={device['Group_Id']}, sub={device['Sub_Id']}): "
             f"전원 {status} 명령 전송? (yes): ").strip() != "yes":
        return
    quiet_call(control_heating_power, api, device, status)
    print("난방 전원 제어 요청 성공. 메뉴 9에서 상태와 실제 기기를 확인하세요.")


def ventilation_snapshot(rows):
    snapshot = {}
    for row in rows:
        if row.get("Device_Type") != "140B":
            continue
        key = tuple(str(row.get(field, "?")) for field in ("Group_Id", "Sub_Id", "Status_Type"))
        if key in snapshot:
            raise ValueError("같은 환기 상태 항목이 중복되어 비교할 수 없습니다.")
        # Only device state values are retained, never the complete response.
        snapshot[key] = json.dumps(row.get("Status"), ensure_ascii=True, sort_keys=True)
    return snapshot


def compare_ventilation_settings(api):
    print("현재 상태를 조회합니다. 아직 월패드·리모컨의 설정을 변경하지 마세요.")
    before = ventilation_snapshot(quiet_call(get_ventilation, api))
    if not before:
        print("비교할 환기 상태가 없습니다.")
        return
    print("변경 전 환기 상태:")
    for (group, sub, field), value in sorted(before.items()):
        print(f"group={group}, sub={sub} | {field}={value}")
    print("이제 월패드·리모컨에서 시간 설정 하나만 변경하세요.")
    print("예: 취침 480분→다른 시간. 예약은 별도로 비교하세요.")
    if input("변경 후 잠시 기다렸다가 Enter (취소: 0): ").strip() == "0":
        return
    after = ventilation_snapshot(quiet_call(get_ventilation, api))
    print("[환기 설정 비교]")
    changed = False
    for group, sub, field in sorted(before.keys() | after.keys()):
        key = (group, sub, field)
        old, new = before.get(key, "[항목 없음]"), after.get(key, "[항목 없음]")
        if old != new:
            changed = True
            print(f"group={group}, sub={sub} | {field}: {old} -> {new}")
    if not changed:
        print("조회된 상태값에 변화가 없습니다. 서버 반영 지연 또는 API 미노출 가능성이 있습니다.")
    print("원래 설정으로 복원하려면 월패드·리모컨에서 직접 변경하세요.")


def control_ventilation_power(api, device, status):
    if not api.access_token:
        raise ValueError("로그인이 필요합니다.")
    if (device.get("Device_Type") != "140B"
            or device.get("Status_Type") != "PowerStatus"
            or status not in ("On", "Off")):
        raise ValueError("환기 전원 항목과 On/Off만 제어할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id,
        "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token,
        "Device_Type": "140B",
        "Group_Id": str(device["Group_Id"]),
        "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": "PowerStatus",
        "Status": status,
    })


def ventilation_power_menu(api):
    devices = quiet_call(get_ventilation, api)
    devices = [row for row in devices if row.get("Device_Type") == "140B"
               and row.get("Status_Type") == "PowerStatus"]
    if not devices:
        print("제어할 수 있는 환기 전원 항목이 없습니다.")
        return
    for index, row in enumerate(devices, 1):
        print(f"{index}. {row.get('Device_Remark', '환기')} | "
              f"PowerStatus={row.get('Status', '?')} | "
              f"group={row.get('Group_Id', '?')}, sub={row.get('Sub_Id', '?')}")
    try:
        index = int(input("환기 번호 (취소: 0): "))
    except ValueError:
        print("목록의 번호를 입력하세요.")
        return
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("환기 번호 범위를 확인하세요.")
        return
    status = input("On / Off: ").strip().capitalize()
    if status not in ("On", "Off"):
        print("On 또는 Off를 입력하세요.")
        return
    device = devices[index - 1]
    if input(f"환기 group={device['Group_Id']}, sub={device['Sub_Id']}: "
             f"전원 {status} 명령 전송? (yes): ").strip() != "yes":
        return
    quiet_call(control_ventilation_power, api, device, status)
    print("환기 전원 제어 요청 성공. 메뉴 4에서 상태와 실제 기기를 확인하세요.")


def control_ventilation_setting(api, device, status, rows):
    field = device.get("Status_Type")
    if not api.access_token or device.get("Device_Type") != "140B":
        raise ValueError("로그인한 환기 기기만 제어할 수 있습니다.")
    if field == "WindPower":
        related = {row.get("Status_Type"): row.get("Status") for row in rows
                   if row.get("Device_Type") == "140B"
                   and str(row.get("Group_Id")) == str(device.get("Group_Id"))
                   and str(row.get("Sub_Id")) == str(device.get("Sub_Id"))}
        minimum, maximum = int(related["SettingMin"]), int(related["SettingMax"])
        value = int(status)
        if not (1 <= value <= 3 and minimum <= value <= maximum):
            raise ValueError("풍량은 기기의 허용 범위 안에서 1~3만 입력할 수 있습니다.")
        status = str(value)
    elif field == "Mode":
        if status not in VENTILATION_MODES:
            raise ValueError("지원하는 환기 모드를 선택하세요.")
    else:
        raise ValueError("풍량 또는 모드만 설정할 수 있습니다.")
    return api._send_request("/device/control", {
        "Complex": api.complex_id, "Access_Token": api.access_token,
        "Refresh_Token": api.refresh_token, "Device_Type": "140B",
        "Group_Id": str(device["Group_Id"]), "Sub_Id": str(device["Sub_Id"]),
        "Status_Type": field, "Status": status,
    })


def change_ventilation_mode(api, device, target):
    if target not in VENTILATION_MODES:
        raise ValueError("지원하지 않는 환기 모드입니다.")

    def current_mode():
        rows = get_ventilation(api)
        for row in rows:
            if (row.get("Device_Type") == "140B" and row.get("Status_Type") == "Mode"
                    and str(row.get("Group_Id")) == str(device.get("Group_Id"))
                    and str(row.get("Sub_Id")) == str(device.get("Sub_Id"))):
                return row, rows
        raise ValueError("선택한 환기의 현재 모드를 확인하지 못했습니다.")

    current, rows = current_mode()
    if current.get("Status") == target:
        return
    if current.get("Status") == "Sleep" and target != "Normal":
        control_ventilation_setting(api, current, "Normal", rows)
        for attempt in range(5):
            if attempt:
                time.sleep(1)
            current, rows = current_mode()
            if current.get("Status") == "Normal":
                break
        else:
            raise ValueError("일반 모드 전환이 확인되지 않아 목표 모드 요청을 중단했습니다.")
    control_ventilation_setting(api, current, target, rows)


def ventilation_setting_menu(api, field):
    rows = quiet_call(get_ventilation, api)
    devices = [row for row in rows if row.get("Device_Type") == "140B"
               and row.get("Status_Type") == field]
    if not devices:
        print("설정할 수 있는 환기 항목이 없습니다.")
        return
    for index, row in enumerate(devices, 1):
        print(f"{index}. {row.get('Device_Remark', '환기')} | {field}={row.get('Status', '?')} | "
              f"group={row.get('Group_Id', '?')}, sub={row.get('Sub_Id', '?')}")
    index = int(input("환기 번호 (취소: 0): "))
    if index == 0:
        return
    if not 1 <= index <= len(devices):
        print("환기 번호 범위를 확인하세요.")
        return
    if field == "Mode":
        choices = dict(enumerate(VENTILATION_MODES, 1))
        print(" / ".join(f"{number}: {VENTILATION_MODES[value]}" for number, value in choices.items()))
        choice = input("모드 번호 (취소: Enter): ").strip()
        if not choice:
            return
        if not choice.isdigit() or int(choice) not in choices:
            print("목록의 모드 번호를 입력하세요.")
            return
        status = choices[int(choice)]
        print("취침 상태에서 전열·자동으로 변경할 때는 일반 전환 확인 후 목표 모드를 요청합니다.")
    else:
        status = input("풍량 (1~3): ").strip()
    if not status:
        return
    device = devices[index - 1]
    if input(f"환기 group={device['Group_Id']}, sub={device['Sub_Id']}: "
             f"{field}={status} 명령 전송? (yes): ").strip() != "yes":
        return
    if field == "Mode":
        try:
            quiet_call(change_ventilation_mode, api, device, status)
        except ValueError as error:
            print(str(error))
            return
    else:
        quiet_call(control_ventilation_setting, api, device, status, rows)
    print("환기 설정 요청 성공. 메뉴 4에서 상태와 실제 기기를 확인하세요.")


def report_auth_shape(response):
    """Report only known field presence; never print authentication values."""
    root = response if isinstance(response, dict) else {}
    resources = root.get("Resources")
    first = (resources[0] if isinstance(resources, list) and resources
             and isinstance(resources[0], dict) else {})
    for label, container in (("응답 최상위", root), ("Resources[0]", first)):
        for field in ("App_Certify", "Certify_Number", "Oauth"):
            value = container.get(field)
            state = "없음" if field not in container else "있음" if value else "빈 값"
            print(f"[인증 진단] {label}.{field}: {state}")


def extract_control_fields(key, payload):
    data = json.loads(quiet_call(decrypt_payload, key, payload))
    if not isinstance(data, dict):
        raise ValueError("요청 본문이 객체가 아닙니다.")
    fields = ("Device_Type", "Group_Id", "Sub_Id", "Status_Type", "Status")
    return {field: data[field] for field in fields
            if field in data and isinstance(data[field], (str, int, float, bool))}


def inspect_control():
    print("mitmweb의 /device/control 요청에서 복사하세요. 입력값은 화면과 파일에 남기지 않습니다.")
    key = getpass.getpass("Request 헤더의 x-message-id 값: ").strip()
    payload = getpass.getpass("같은 Request의 암호화 본문 (한 줄): ").strip()
    try:
        fields = extract_control_fields(key, payload)
    except Exception:
        print("복호화 실패: 같은 요청의 헤더와 본문인지 확인하세요.", file=sys.stderr)
        return 1
    if not fields:
        print("확인할 제어 항목이 없습니다.", file=sys.stderr)
        return 1
    print("아래 결과만 공유하세요. 계정·토큰과 원본 요청은 출력하지 않습니다.")
    print(json.dumps(fields, ensure_ascii=True, indent=2))
    return 0


def interactive(auth_diagnostics=False):
    keyword = input("단지 검색어 (전체: Enter): ").strip().casefold()
    complexes = quiet_call(KDOneAPI.get_complexes)
    matches = [c for c in complexes if keyword in f"{c.name} {c.address}".casefold()]
    for index, item in enumerate(matches, 1):
        print(f"{index}. {item.name} | {item.address} | ID={item.id}")
    if not matches:
        print("일치하는 단지가 없습니다.")
        return
    index = int(input("단지 번호: "))
    if not 1 <= index <= len(matches):
        raise ValueError("단지 번호 범위를 확인하세요.")
    complex_id = matches[index - 1].id
    username = input("나비엔 홈네트워크 아이디: ").strip()
    password = getpass.getpass("비밀번호 (화면에 표시되지 않음): ")
    api = KDOneAPI(username, password, verbose=False)
    # Request the wallpad code explicitly. Upstream login() requests it only
    # after HTTP 404, so a successful initial login can skip code delivery.
    api.complex_id = complex_id
    print("월패드 인증번호를 요청합니다. 서버 응답을 기다려 주세요.", flush=True)
    quiet_call(api._get_certification_code, _diagnostics=auth_diagnostics)
    print("서버가 인증번호 요청을 처리했습니다. 월패드에 표시되면 입력하세요.", flush=True)
    code = getpass.getpass("월패드 인증번호 (화면에 표시되지 않음): ").strip()
    if not code:
        print("월패드 인증번호가 비어 있습니다. 다시 실행하여 입력하세요.")
        return
    certification = quiet_call(api.certify, code)
    if auth_diagnostics:
        report_auth_shape(certification)
    print("월패드 인증번호 검증 완료. 로그인 토큰을 요청합니다.")
    quiet_call(api.get_token, _diagnostics=auth_diagnostics)
    if not api.access_token:
        raise ValueError("액세스 토큰이 발급되지 않았습니다.")
    print("인증 완료. 계정 정보와 토큰은 파일에 저장하지 않습니다.")
    while True:
        command = input("\n1: 조명 조회 / 2: 조명 제어 / 3: 엘리베이터 호출 / "
                        "4: 환기 조회 / 5: 환기 전원 제어 / "
                        "6: 환기 풍량 / 7: 환기 모드 / 8: 환기 시간·예약 비교 / "
                        "9: 난방 조회 / 10: 난방 전원 제어 / 11: 난방 온도 설정 / "
                        "12: 난방 외출 모드 / 13: 가스·전기레인지 조회 / "
                        "14: 가스밸브 차단 / 15: 쿡탑 차단 / "
                        "16: 가스밸브 열기 시험 / 17: 쿡탑 켜기 시험 / 0: 종료 > ").strip()
        if command == "0":
            return
        if command == "1":
            show_devices(api)
        elif command in ("16", "17"):
            try:
                hybrid_enable_test_menu(api, "GasValveStatus" if command == "16" else "InductionStatus")
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("켜기 시험 실패: 대상 번호 또는 응답 형식을 확인하세요.")
        elif command in ("14", "15"):
            try:
                if command == "14":
                    hybrid_shutdown_menu(api)
                else:
                    induction_off_menu(api)
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("차단 요청 실패: 대상 번호 또는 응답 형식을 확인하세요.")
        elif command == "13":
            try:
                show_hybrid(api)
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("가스·전기레인지 조회 실패: 서버 지원 여부와 응답 형식을 확인하세요.")
        elif command in ("10", "11", "12"):
            try:
                if command == "10":
                    heating_power_menu(api)
                elif command == "11":
                    heating_temperature_menu(api)
                else:
                    heating_away_menu(api)
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("난방 설정 실패: 입력값 또는 응답 형식을 확인하세요.")
        elif command == "9":
            try:
                show_heating(api)
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("난방 조회 실패: 서버 지원 여부와 응답 형식 확인이 필요합니다.")
        elif command == "8":
            try:
                compare_ventilation_settings(api)
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("환기 상태를 비교하지 못했습니다. 응답 형식 확인이 필요합니다.")
        elif command == "4":
            try:
                show_ventilation(api)
            except ConsoleRequestError as error:
                print(f"환기 조회 실패: {error}")
            except (ValueError, KeyError, TypeError):
                print("환기 조회 응답을 처리하지 못했습니다. 서버 지원 여부와 응답 형식 확인이 필요합니다.")
        elif command in ("5", "6", "7"):
            try:
                if command == "5":
                    ventilation_power_menu(api)
                else:
                    ventilation_setting_menu(api, "WindPower" if command == "6" else "Mode")
            except ConsoleRequestError as error:
                print(str(error))
            except (ValueError, KeyError, TypeError):
                print("환기 설정 실패: 입력값, 기기 설정 범위 또는 응답 형식을 확인하세요.")
        elif command == "2":
            devices = show_devices(api)
            if not devices:
                continue
            index = int(input("제어할 조명 번호: "))
            if not 1 <= index <= len(devices):
                print("조명 번호 범위를 확인하세요.")
                continue
            device = devices[index - 1]
            if device.status_type != StatusType.OnOffStatus:
                print("현재 콘솔은 OnOffStatus 항목만 제어합니다.")
                continue
            status = input("On / Off: ").strip().capitalize()
            if status not in ("On", "Off"):
                print("On 또는 Off를 입력하세요.")
                continue
            if input(f"{device.remark}: {status} 명령 전송? (yes): ").strip() == "yes":
                quiet_call(api.control_device, device, status)
                print("제어 요청 성공. 조회 메뉴에서 상태를 확인하세요.")
        elif command == "3":
            if input("실제 엘리베이터 호출? (yes): ").strip() == "yes":
                quiet_call(api.call_elevator)
                print("엘리베이터 호출 요청 성공.")


def main():
    parser = argparse.ArgumentParser(description="KDOne 콘솔 테스트")
    parser.add_argument("--self-test", action="store_true", help="실제 연결 없이 자체 점검")
    parser.add_argument("--complexes", action="store_true", help="계정 없이 실제 단지 목록 조회")
    parser.add_argument("--inspect-control", action="store_true",
                        help="캡처한 제어 요청을 로컬에서 복호화하고 제어 항목만 출력")
    parser.add_argument("--auth-diagnostics", action="store_true",
                        help="인증 응답의 알려진 필드 존재 여부만 출력 (값은 숨김)")
    args = parser.parse_args()
    if args.inspect_control:
        try:
            return inspect_control()
        except (KeyboardInterrupt, EOFError):
            print("\n종료했습니다.")
            return 0
    original_post = requests.post

    def bounded_post(*positional, **kwargs):
        kwargs.setdefault("timeout", (10, 30))
        return original_post(*positional, **kwargs)

    try:
        with patch("requests.post", side_effect=bounded_post):
            if args.self_test:
                self_test()
            elif args.complexes:
                items = quiet_call(KDOneAPI.get_complexes)
                for item in items:
                    print(f"{item.id}\t{item.name}\t{item.address}")
                print(f"총 {len(items)}개 단지")
            else:
                interactive(auth_diagnostics=args.auth_diagnostics)
    except ConsoleRequestError as error:
        print(str(error), file=sys.stderr)
        return 1
    except requests.exceptions.SSLError:
        print("TLS 인증서 검증 실패: Windows 신뢰 인증서 또는 REQUESTS_CA_BUNDLE 설정을 확인하세요.", file=sys.stderr)
        return 1
    except requests.RequestException:
        print("연결 실패: 네트워크, 서버 상태 또는 HTTP 응답을 확인하세요.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, IndexError):
        print("입력값 또는 서버 응답이 예상 형식과 다릅니다. 단지/계정/인증번호를 확인하세요.", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n종료했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
