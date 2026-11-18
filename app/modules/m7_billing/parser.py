"""计费输入解析：航段与共享航班关系，所有问题在采信阶段隔离并保留证据。"""
from datetime import datetime

from . import (
    REASON_BAD_NATURE, REASON_BAD_TIMESTAMP, REASON_CONFLICT, REASON_DUPLICATE,
    REASON_EMPTY, REASON_MAPPING_CONFLICT, REASON_UNKNOWN_REFERENCE,
)

LEG_FIELDS = (
    "flight_no", "operating_flight_no", "flight_date", "origin", "destination",
    "actual_departure", "aircraft_reg", "flight_nature",
)
LEG_REQUIRED = ("flight_no", "flight_date", "origin", "destination", "actual_departure")
MAPPING_FIELDS = ("marketing_flight_no", "operating_flight_no", "flight_date", "origin", "destination")
NATURES = ("NORMAL", "EXTRA")


def _clean(row: dict) -> dict:
    return {key: str(row.get(key, "") or "").strip() for key in row}


def leg_identity(row: dict) -> tuple:
    """航班身份（不含性质）：共享关系与结清跟踪使用。"""
    return (row["flight_no"], row["flight_date"], row["origin"], row["destination"])


def _dedup_key(row: dict) -> tuple:
    # 同一航班号的正常班与加班班是两条独立记录，性质必须参与去重键
    return (*leg_identity(row), row["flight_nature"])


def parse_legs(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """采信航段。返回 (有效航段, 隔离记录)，隔离记录带行号、原因码与原值。"""
    accepted, quarantine, seen = [], [], {}
    for line_no, source in enumerate(rows, 2):  # 表头占物理第 1 行
        row = _clean(source)
        if any(not row.get(key) for key in LEG_REQUIRED):
            quarantine.append({"line_no": line_no, "reason_code": REASON_EMPTY, "raw": source})
            continue
        if not _valid_timestamp(row["actual_departure"]):
            quarantine.append({"line_no": line_no, "reason_code": REASON_BAD_TIMESTAMP, "raw": source})
            continue
        row["flight_nature"] = (row.get("flight_nature") or "NORMAL").upper()
        if row["flight_nature"] not in NATURES:
            quarantine.append({"line_no": line_no, "reason_code": REASON_BAD_NATURE, "raw": source})
            continue
        for code in ("flight_no", "operating_flight_no", "origin", "destination", "aircraft_reg"):
            row[code] = row[code].upper()
        key = _dedup_key(row)
        if key in seen:
            code = REASON_DUPLICATE if seen[key] == row else REASON_CONFLICT
            quarantine.append({"line_no": line_no, "reason_code": code, "raw": source})
            continue
        seen[key] = dict(row)
        row["line_no"] = line_no
        accepted.append(row)
    return accepted, quarantine


def parse_codeshares(rows: list[dict], accepted_legs: list[dict]) -> tuple[dict, list[dict]]:
    """采信共享航班关系。

    返回 (mappings, quarantine)。mappings 以营销航班身份键为键，值为执飞航班号。
    同一营销键映射到不同执飞号 → MAPPING_CONFLICT；营销号在航段里不存在 → UNKNOWN_REFERENCE。
    """
    leg_identities = {leg_identity(leg) for leg in accepted_legs}
    mappings, quarantine = {}, []
    for line_no, source in enumerate(rows, 2):
        row = _clean(source)
        if any(not row.get(key) for key in MAPPING_FIELDS):
            quarantine.append({"line_no": line_no, "reason_code": REASON_EMPTY, "raw": source})
            continue
        row = {key: row[key].upper() for key in MAPPING_FIELDS}
        marketing_key = (row["marketing_flight_no"], row["flight_date"], row["origin"], row["destination"])
        if marketing_key not in leg_identities:
            quarantine.append({"line_no": line_no, "reason_code": REASON_UNKNOWN_REFERENCE, "raw": source})
            continue
        if marketing_key in mappings and mappings[marketing_key] != row["operating_flight_no"]:
            quarantine.append({"line_no": line_no, "reason_code": REASON_MAPPING_CONFLICT, "raw": source})
            continue
        mappings[marketing_key] = row["operating_flight_no"]
    return mappings, quarantine


def _valid_timestamp(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True
