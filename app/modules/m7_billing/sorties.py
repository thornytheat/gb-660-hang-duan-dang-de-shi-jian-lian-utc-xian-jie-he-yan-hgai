"""架次认定纯函数：把采信航段归并为可计费的实际起飞架次。

不做任何猜测性并单：缺执飞航班号的航段一律保持 UNVERIFIED 挂账，
最多给出"疑似重复"提示供人工复核，提示永不自动改变架次。
"""
from decimal import Decimal
from hashlib import sha256

from . import REASON_MISSING_OPERATING, REASON_REG_CONFLICT

DEFAULT_FEE = "1200.00"


def _identity(row: dict) -> tuple:
    return (row["flight_no"], row["flight_date"], row["origin"], row["destination"])


def _sortie_key(row: dict, operating_no: str) -> tuple:
    # 航班性质进键：同航线同刻的加班机因此成为独立架次
    return (operating_no, row["flight_date"], row["origin"], row["destination"],
            row["actual_departure"], row["flight_nature"])


def sortie_id_for(key: tuple) -> str:
    return "S-" + sha256("|".join(key).encode("utf-8")).hexdigest()[:12]


def _airline_prefix(flight_no: str) -> str:
    prefix = ""
    for char in flight_no:
        if char.isalpha():
            prefix += char
        else:
            break
    return prefix[:2]


def _fee_for(operating_no: str, fee_schedule: list[dict]) -> Decimal:
    airline = _airline_prefix(operating_no)
    for item in fee_schedule:
        if item["airline"].upper() == airline:
            return Decimal(str(item["fee"]))
    return Decimal(DEFAULT_FEE)


def resolve(accepted_legs: list[dict], codeshare_mappings: dict,
            fee_schedule: list[dict]) -> dict:
    groups: dict[tuple, dict] = {}
    unverified: list[dict] = []
    quarantine: list[dict] = []

    for leg in accepted_legs:
        operating_no = leg.get("operating_flight_no") or codeshare_mappings.get(_identity(leg), "")
        if not operating_no:
            unverified.append({**leg, "status": "UNVERIFIED",
                               "reason_code": REASON_MISSING_OPERATING})
            continue
        key = _sortie_key(leg, operating_no)
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "sortie_id": sortie_id_for(key),
                "operating_flight_no": operating_no,
                "flight_date": leg["flight_date"],
                "origin": leg["origin"],
                "destination": leg["destination"],
                "actual_departure": leg["actual_departure"],
                "flight_nature": leg["flight_nature"],
                "aircraft_reg": leg.get("aircraft_reg", ""),
                "flight_numbers": [leg["flight_no"]],
                "source_line_nos": [leg["line_no"]],
                "reg_seen": {leg["aircraft_reg"]} if leg.get("aircraft_reg") else set(),
            }
            continue
        group["flight_numbers"].append(leg["flight_no"])
        group["source_line_nos"].append(leg["line_no"])
        reg = leg.get("aircraft_reg", "")
        if reg and reg not in group["reg_seen"] and group["reg_seen"]:
            quarantine.append({
                "line_no": leg["line_no"], "reason_code": REASON_REG_CONFLICT,
                "sortie_id": group["sortie_id"],
                "raw": {"flight_no": leg["flight_no"], "aircraft_reg": reg,
                        "expected_reg": group["aircraft_reg"]},
            })
        if reg:
            group["reg_seen"].add(reg)

    sorties = []
    total_fee = Decimal("0")
    for key, group in sorted(groups.items()):
        fee = _fee_for(group["operating_flight_no"], fee_schedule)
        total_fee += fee
        group.pop("reg_seen")
        group["flight_numbers"] = sorted(set(group["flight_numbers"]))
        group["source_line_nos"] = sorted(set(group["source_line_nos"]))
        group["fee"] = str(fee)
        group["status"] = "CHARGED"
        group["charged"] = True
        sorties.append(group)

    hints = _duplicate_hints(unverified)
    member_legs = sum(len(s["source_line_nos"]) for s in sorties)
    totals = {
        "legs_in": len(accepted_legs),
        "member_legs": member_legs,
        "unverified_legs": len(unverified),
        "sorties": len(sorties),
        "charged_sorties": len(sorties),
        "quarantined_legs": len(quarantine),
        "total_fee": str(total_fee),
        "balanced": len(accepted_legs) == member_legs + len(unverified),
    }
    return {"sorties": sorties, "unverified": unverified, "hints": hints,
            "quarantine": quarantine, "totals": totals}


def _duplicate_hints(unverified: list[dict]) -> list[dict]:
    """同日期/航线/实际起飞时刻/机号的未核实航段才提示疑似重复；无机号不提示。"""
    buckets: dict[tuple, list[dict]] = {}
    for leg in unverified:
        if not leg.get("aircraft_reg"):
            continue
        bucket_key = (leg["flight_date"], leg["origin"], leg["destination"],
                      leg["actual_departure"], leg["aircraft_reg"])
        buckets.setdefault(bucket_key, []).append(leg)
    hints = []
    for bucket_key, legs in buckets.items():
        if len(legs) < 2:
            continue
        hints.append({
            "code": "POSSIBLE_DUPLICATE_SORTIE",
            "message": "两条未核实航段机号与实际起飞时刻相同，疑似同一架次，待人工补执飞号确认",
            "line_nos": sorted(leg["line_no"] for leg in legs),
            "flight_numbers": sorted(leg["flight_no"] for leg in legs),
            "aircraft_reg": bucket_key[-1],
        })
    return sorted(hints, key=lambda hint: hint["line_nos"])
