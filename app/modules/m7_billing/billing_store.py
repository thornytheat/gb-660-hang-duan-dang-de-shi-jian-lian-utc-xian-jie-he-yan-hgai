"""M7 持久化：批次、计费架次、未结清航段与隔离证据。

防重复计费有两道防线：
1. 同输入哈希 → 同 run_id，整批幂等复用；
2. billing_sorties 对 charged=1 的架次键建部分唯一索引，
   即使纠正数据后产生新批次，同一物理架次也无法被第二次收费。
"""
import json

from app.store import connect


def init_billing_db():
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS billing_runs (
                run_id TEXT PRIMARY KEY,
                input_sha256 TEXT NOT NULL,
                rule_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS billing_sorties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                sortie_id TEXT NOT NULL,
                sortie_key TEXT NOT NULL,
                charged INTEGER NOT NULL,
                sortie_json TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_charged_key
                ON billing_sorties(sortie_key) WHERE charged = 1;
            CREATE TABLE IF NOT EXISTS billing_unverified (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                identity TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                leg_json TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS billing_quarantine (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                reason_code TEXT NOT NULL,
                raw_json TEXT NOT NULL
            );
            """
        )


def get_run(run_id: str) -> dict | None:
    init_billing_db()
    with connect() as db:
        row = db.execute(
            "SELECT result_json FROM billing_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def run_exists(run_id: str) -> bool:
    init_billing_db()
    with connect() as db:
        return db.execute(
            "SELECT 1 FROM billing_runs WHERE run_id = ?", (run_id,)
        ).fetchone() is not None


def charged_keys() -> set[str]:
    init_billing_db()
    with connect() as db:
        rows = db.execute(
            "SELECT sortie_key FROM billing_sorties WHERE charged = 1"
        ).fetchall()
    return {row[0] for row in rows}


def charged_identities() -> set[tuple]:
    """所有已计费架次覆盖的航班身份（flight_no, date, origin, destination）。"""
    init_billing_db()
    identities = set()
    with connect() as db:
        rows = db.execute(
            "SELECT sortie_json FROM billing_sorties WHERE charged = 1"
        ).fetchall()
    for (payload,) in rows:
        sortie = json.loads(payload)
        for flight_no in sortie["flight_numbers"]:
            identities.add((flight_no, sortie["flight_date"],
                            sortie["origin"], sortie["destination"]))
    return identities


def save_run(run_id: str, input_sha256: str, rule_version: str, result: dict,
             sortie_keys: list[tuple], quarantine_legs: list[dict],
             quarantine_mappings: list[dict]) -> None:
    init_billing_db()
    with connect() as db:
        db.execute(
            "INSERT INTO billing_runs VALUES (?, ?, ?, datetime('now'), ?)",
            (run_id, input_sha256, rule_version, json.dumps(result, ensure_ascii=False)),
        )
        for key, sortie in zip(sortie_keys, result["sorties"]):
            db.execute(
                "INSERT INTO billing_sorties (run_id, sortie_id, sortie_key, charged, sortie_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, sortie["sortie_id"], "|".join(key),
                 1 if sortie["charged"] else 0, json.dumps(sortie, ensure_ascii=False)),
            )
        for leg in result["unverified"]:
            identity = "|".join((leg["flight_no"], leg["flight_date"],
                                 leg["origin"], leg["destination"]))
            db.execute(
                "INSERT INTO billing_unverified (run_id, line_no, identity, reason_code, leg_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (run_id, leg["line_no"], identity, leg["reason_code"],
                 json.dumps(leg, ensure_ascii=False)),
            )
        for item in quarantine_legs:
            db.execute(
                "INSERT INTO billing_quarantine (run_id, source, line_no, reason_code, raw_json)"
                " VALUES (?, 'LEG', ?, ?, ?)",
                (run_id, item["line_no"], item["reason_code"], json.dumps(item["raw"], ensure_ascii=False)),
            )
        for item in quarantine_mappings:
            db.execute(
                "INSERT INTO billing_quarantine (run_id, source, line_no, reason_code, raw_json)"
                " VALUES (?, 'CODESHARE', ?, ?, ?)",
                (run_id, item["line_no"], item["reason_code"], json.dumps(item["raw"], ensure_ascii=False)),
            )
        for item in result["quarantine"]:  # 架次级证据（机号冲突等）
            db.execute(
                "INSERT INTO billing_quarantine (run_id, source, line_no, reason_code, raw_json)"
                " VALUES (?, 'SORTIE', ?, ?, ?)",
                (run_id, item["line_no"], item["reason_code"], json.dumps(item["raw"], ensure_ascii=False)),
            )


def mark_resolved(identities: set[tuple]) -> None:
    """已在某计费架次中出现的航班身份，对应历史未核实记录视为结清。"""
    if not identities:
        return
    init_billing_db()
    with connect() as db:
        for identity in identities:
            db.execute(
                "UPDATE billing_unverified SET resolved = 1 WHERE identity = ?",
                ("|".join(identity),),
            )


def unsettled() -> list[dict]:
    init_billing_db()
    settled = charged_identities()
    items: list[dict] = []
    with connect() as db:
        unverified = db.execute(
            "SELECT leg_json FROM billing_unverified WHERE resolved = 0"
        ).fetchall()
        quarantined = db.execute(
            "SELECT raw_json, reason_code, line_no, source FROM billing_quarantine"
        ).fetchall()
    for (payload,) in unverified:
        leg = json.loads(payload)
        identity = (leg["flight_no"], leg["flight_date"], leg["origin"], leg["destination"])
        if identity not in settled:
            items.append({"source": "UNVERIFIED", "line_no": leg["line_no"],
                          "flight_no": leg["flight_no"], "reason_code": leg["reason_code"]})
    for raw_json, reason_code, line_no, source in quarantined:
        raw = json.loads(raw_json)
        flight_no = raw.get("flight_no") or raw.get("marketing_flight_no", "")
        items.append({"source": source, "line_no": line_no,
                      "flight_no": flight_no, "reason_code": reason_code})
    return sorted(items, key=lambda item: (item["source"], item["line_no"]))
