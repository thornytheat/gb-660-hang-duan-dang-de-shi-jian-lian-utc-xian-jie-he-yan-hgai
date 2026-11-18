"""M7 服务编排：解析 → 架次认定 → 跨批次防重 → 持久化。"""
import json
from decimal import Decimal
from hashlib import sha256

from app.config import BILLING_RULE_VERSION
from . import billing_store
from .parser import parse_codeshares, parse_legs
from .sorties import DEFAULT_FEE, resolve

__all__ = ["DEFAULT_FEE", "create_run", "get_run", "unsettled_legs", "demo"]


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def run_id_for(payload: dict) -> tuple[str, str]:
    digest = sha256(_canonical_bytes(payload)).hexdigest()
    return f"{BILLING_RULE_VERSION}-{digest[:12]}", digest


def _sortie_key(sortie: dict) -> tuple:
    return (sortie["operating_flight_no"], sortie["flight_date"], sortie["origin"],
            sortie["destination"], sortie["actual_departure"], sortie["flight_nature"])


def create_run(payload: dict) -> dict:
    run_id, digest = run_id_for(payload)
    existing = billing_store.get_run(run_id)
    if existing is not None:
        existing["created"] = False
        existing["reused"] = True
        return existing

    accepted, leg_quarantine = parse_legs(payload.get("legs", []))
    mappings, mapping_quarantine = parse_codeshares(payload.get("codeshares", []), accepted)
    result = resolve(accepted, mappings, payload.get("fee_schedule", []))

    already_charged = billing_store.charged_keys()
    charged_fee = Decimal("0")
    sortie_keys = []
    charged_identities = set()
    for sortie in result["sorties"]:
        key = _sortie_key(sortie)
        sortie_keys.append(key)
        if "|".join(key) in already_charged:
            sortie["status"] = "ALREADY_BILLED"
            sortie["charged"] = False
        else:
            charged_fee += Decimal(sortie["fee"])
            for flight_no in sortie["flight_numbers"]:
                charged_identities.add((flight_no, sortie["flight_date"],
                                        sortie["origin"], sortie["destination"]))

    totals = result["totals"]
    totals["charged_sorties"] = sum(1 for s in result["sorties"] if s["charged"])
    totals["total_fee"] = str(charged_fee)
    totals["quarantined_legs"] = (totals["quarantined_legs"] + len(leg_quarantine)
                                  + len(mapping_quarantine))
    result["run_id"] = run_id
    result["input_sha256"] = digest
    result["rule_version"] = BILLING_RULE_VERSION
    result["created"] = True
    result["reused"] = False

    billing_store.save_run(run_id, digest, BILLING_RULE_VERSION, result,
                           sortie_keys, leg_quarantine, mapping_quarantine)
    billing_store.mark_resolved(charged_identities)
    return result


def get_run(run_id: str) -> dict | None:
    return billing_store.get_run(run_id)


def unsettled_legs() -> list[dict]:
    return billing_store.unsettled()


def demo() -> dict:
    return create_run({
        "legs": [
            {"flight_no": "CA7001", "operating_flight_no": "", "flight_date": "2026-11-16",
             "origin": "PVG", "destination": "PEK", "actual_departure": "2026-11-16T09:05",
             "aircraft_reg": "B-6543", "flight_nature": "NORMAL"},
            {"flight_no": "MU5101", "operating_flight_no": "MU5101", "flight_date": "2026-11-16",
             "origin": "PVG", "destination": "PEK", "actual_departure": "2026-11-16T09:05",
             "aircraft_reg": "B-6543", "flight_nature": "NORMAL"},
            {"flight_no": "MU5101", "operating_flight_no": "MU5101", "flight_date": "2026-11-16",
             "origin": "PVG", "destination": "PEK", "actual_departure": "2026-11-16T09:05",
             "aircraft_reg": "B-6543", "flight_nature": "EXTRA"},
        ],
        "codeshares": [
            {"marketing_flight_no": "CA7001", "operating_flight_no": "MU5101",
             "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
        ],
        "fee_schedule": [],
    })
