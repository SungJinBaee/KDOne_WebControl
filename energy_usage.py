"""Read-only /used/list contract observed in Android app 4.14.04.

Category_Type: Electricity/Water/Heating/Hotwater/Gas.
Query_Type: Day/Month. Resources: Start_Time, Amount, Unit.
Production NAS response uses epoch seconds; also accept epoch milliseconds.
Never call energy-used-target-insert (it changes notification settings).
"""
import calendar
import math
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

CATEGORIES = {"Electricity": "전기", "Water": "수도", "Heating": "난방",
              "Hotwater": "온수", "Gas": "가스"}
KOREA = timezone(timedelta(hours=9))


def reading_date(raw):
    """Accept 10-digit seconds or 13-digit milliseconds in Korea time."""
    numeric = str(raw) if type(raw) is int else raw
    if isinstance(numeric, str) and re.fullmatch(r"(?:[0-9]{10}|[0-9]{13})", numeric):
        milliseconds = int(numeric) * (1000 if len(numeric) == 10 else 1)
        try:
            return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=milliseconds)).astimezone(KOREA).date()
        except (ValueError, OverflowError):
            raise ValueError("사용량 응답의 날짜가 올바르지 않습니다.") from None
    if isinstance(raw, str) and re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T|$| )", raw):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            raise ValueError("사용량 응답의 날짜가 올바르지 않습니다.") from None
    raise ValueError("사용량 응답의 날짜 형식을 확인하지 못했습니다.")


def query_ranges(category, period, selected, compare, today=None):
    today = today or datetime.now(timezone(timedelta(hours=9))).date()
    if category not in CATEGORIES or period not in ("Day", "Month") or not isinstance(selected, str):
        raise ValueError("사용량 종류와 조회 기간을 확인하세요.")
    if compare not in ("0", "1"):
        raise ValueError("전년 비교 여부를 확인하세요.")
    if period == "Day":
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", selected):
            raise ValueError("일별 조회는 YYYY-MM 형식으로 선택하세요.")
        year, month = map(int, selected.split("-"))
    else:
        if not re.fullmatch(r"[0-9]{4}", selected):
            raise ValueError("월별 조회는 YYYY 형식으로 선택하세요.")
        year, month = int(selected), 1
    if not 2014 <= year <= today.year or not 1 <= month <= 12:
        raise ValueError("조회 가능한 연도와 월을 선택하세요.")
    start = date(year, month, 1)
    if start > today:
        raise ValueError("미래의 사용량은 조회할 수 없습니다.")
    end = date(year, month, calendar.monthrange(year, month)[1]) if period == "Day" else date(year, 12, 31)
    end = min(end, today)
    ranges = [("current", start, end)]
    if compare == "1":
        # App comparison uses the whole previous month/year, not elapsed days.
        previous_end = (date(year-1, month, calendar.monthrange(year-1, month)[1])
                        if period == "Day" else date(year-1, 12, 31))
        ranges.append(("previous", date(year-1, month, 1), previous_end))
    return ranges


def normalize_series(payload, start, end, period):
    rows = payload.get("Resources") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 800:
        raise ValueError("사용량 응답의 목록 형식을 확인하지 못했습니다.")
    points, seen, dates = [], set(), []
    first_timestamp = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("사용량 응답 항목 형식이 다릅니다.")
        day = reading_date(row.get("Start_Time"))
        dates.append(day)
        if first_timestamp is None:
            raw = row.get("Start_Time")
            if type(raw) is int or (isinstance(raw, str) and re.fullmatch(r"(?:[0-9]{10}|[0-9]{13})", raw)):
                first_timestamp = str(raw)[:20]
        if not start <= day <= end:
            continue
        key = day.isoformat() if period == "Day" else day.strftime("%Y-%m")
        if key in seen:
            raise ValueError("같은 기간의 사용량이 중복되어 합계를 계산하지 않았습니다.")
        seen.add(key)
        unit, raw = row.get("Unit"), row.get("Amount")
        if not isinstance(unit, str) or len(unit) > 24 or isinstance(raw, bool):
            raise ValueError("사용량 응답의 단위 또는 수치 형식이 다릅니다.")
        if raw is None or raw == "":
            amount = None
        else:
            try:
                decimal = Decimal(str(raw))
                amount = float(decimal)
                if not decimal.is_finite() or not math.isfinite(amount) or amount < 0:
                    raise ValueError
            except (InvalidOperation, ValueError, OverflowError):
                raise ValueError("사용량 응답의 수치를 확인하지 못했습니다.") from None
        points.append({"date": key, "amount": amount, "unit": unit})
    points.sort(key=lambda item: item["date"])
    units = {item["unit"] for item in points}
    unit = next(iter(units)) if len(units) == 1 else None
    # Missing readings and differing units must never silently become a zero total.
    total = float(sum(Decimal(str(item["amount"])) for item in points)) if points and unit and all(item["amount"] is not None for item in points) else None
    if total is not None and not math.isfinite(total):
        raise ValueError("사용량 합계가 표시 가능한 범위를 벗어났습니다.")
    diagnostics = {"received": len(rows), "in_range": len(points),
                   "missing_amount": sum(item["amount"] is None for item in points),
                   "response_start": min(dates).isoformat() if dates else None,
                   "response_end": max(dates).isoformat() if dates else None,
                   "first_timestamp": first_timestamp,
                   "amount_field_present": bool(rows and "Amount" in rows[0])}
    return {"start": start.isoformat(), "end": end.isoformat(), "points": points,
            "unit": unit, "total": total, "diagnostics": diagnostics}


def demo_payload(category, start, end, period):
    units = {"Electricity": "kWh", "Water": "㎥", "Heating": "MWh", "Hotwater": "㎥", "Gas": "㎥"}
    rows, day = [], start
    while day <= end:
        rows.append({"Start_Time": day.isoformat(), "Amount": str(round((day.month + day.day + day.year % 7) * .37, 2)), "Unit": units[category]})
        day = day + timedelta(days=1) if period == "Day" else date(day.year+int(day.month == 12), day.month % 12 + 1, 1)
    return {"Resources": rows}
