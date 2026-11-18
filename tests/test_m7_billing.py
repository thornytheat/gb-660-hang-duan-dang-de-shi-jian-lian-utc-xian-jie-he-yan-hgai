import csv
import io
import pytest
from app import store
from app.modules.m7_billing.parser import parse_legs, parse_codeshares
from app.modules.m7_billing.sorties import resolve
from app.modules.m7_billing import service
from app.modules.m7_billing.exporter import statement_csv, statement_json


def leg(flight_no, operating="", date="2026-11-16", origin="PVG", dest="PEK",
        departure="2026-11-16T09:05", reg="B-6543", nature="NORMAL"):
    return {"flight_no": flight_no, "operating_flight_no": operating, "flight_date": date,
            "origin": origin, "destination": dest, "actual_departure": departure,
            "aircraft_reg": reg, "flight_nature": nature}


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "billing.sqlite")
    return tmp_path


# ---------- 解析与隔离 ----------

def test_missing_actual_departure_quarantined():
    accepted, quarantine = parse_legs([leg("MU5101", "MU5101", departure="")])
    assert not accepted and quarantine[0]["reason_code"] == "EMPTY_VALUE"
    assert quarantine[0]["line_no"] == 2  # CSV 表头占第 1 行


def test_bad_timestamp_and_nature_quarantined():
    _, bad_time = parse_legs([leg("MU5101", "MU5101", departure="not-a-time")])
    _, bad_nature = parse_legs([leg("MU5101", "MU5101", nature="MAGIC")])
    assert bad_time[0]["reason_code"] == "BAD_TIMESTAMP"
    assert bad_nature[0]["reason_code"] == "BAD_FLIGHT_NATURE"


def test_duplicate_and_conflict_detected():
    rows = [leg("MU5101", "MU5101"), leg("MU5101", "MU5101")]
    accepted, quarantine = parse_legs(rows)
    assert len(accepted) == 1 and quarantine[0]["reason_code"] == "DUPLICATE_KEY"
    rows[1] = leg("MU5101", "MU5101", departure="2026-11-16T09:40")
    _, quarantine = parse_legs(rows)
    assert quarantine[0]["reason_code"] == "VALUE_CONFLICT"


def test_codeshare_mapping_conflict_and_unknown_reference():
    accepted, _ = parse_legs([leg("CA7001"), leg("MU5101", "MU5101")])
    mappings, quarantine = parse_codeshares([
        {"marketing_flight_no": "CA7001", "operating_flight_no": "MU5101",
         "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
        {"marketing_flight_no": "CA7001", "operating_flight_no": "CZ9999",
         "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
    ], accepted)
    assert quarantine[0]["reason_code"] == "MAPPING_CONFLICT"
    _, unknown = parse_codeshares([
        {"marketing_flight_no": "ZZ0001", "operating_flight_no": "MU5101",
         "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
    ], accepted)
    assert unknown[0]["reason_code"] == "UNKNOWN_REFERENCE"


# ---------- 架次认定 ----------

def test_codeshare_flights_count_as_one_sortie():
    accepted, _ = parse_legs([leg("CA7001"), leg("MU5101", "MU5101")])
    mappings, _ = parse_codeshares([
        {"marketing_flight_no": "CA7001", "operating_flight_no": "MU5101",
         "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
    ], accepted)
    result = resolve(accepted, mappings, [])
    assert result["totals"]["sorties"] == 1
    sortie = result["sorties"][0]
    assert set(sortie["flight_numbers"]) == {"CA7001", "MU5101"}
    assert sortie["fee"] == service.DEFAULT_FEE


def test_extra_flight_same_route_same_time_billed_separately():
    accepted, _ = parse_legs([
        leg("MU5101", "MU5101", nature="NORMAL"),
        leg("MU5101", "MU5101", nature="EXTRA"),
    ])
    result = resolve(accepted, {}, [])
    natures = {s["flight_nature"] for s in result["sorties"]}
    assert result["totals"]["sorties"] == 2 and natures == {"NORMAL", "EXTRA"}
    assert result["totals"]["charged_sorties"] == 2


def test_two_legs_without_operating_no_are_unverified_not_guessed():
    accepted, _ = parse_legs([leg("CZ3002", operating=""), leg("HU7301", operating="")])
    result = resolve(accepted, {}, [])
    assert result["totals"]["sorties"] == 0
    assert result["totals"]["charged_sorties"] == 0
    assert {l["reason_code"] for l in result["unverified"]} == {"OPERATING_FLIGHT_NO_MISSING"}
    hint = result["hints"][0]
    assert hint["code"] == "POSSIBLE_DUPLICATE_SORTIE"
    assert set(hint["line_nos"]) == {2, 3}
    # 提示只作参考，未核实航段绝不产生计费架次
    assert all(l["status"] == "UNVERIFIED" for l in result["unverified"])


def test_unverified_legs_without_shared_reg_get_no_merge_hint():
    accepted, _ = parse_legs([
        leg("CZ3002", operating="", reg="B-1111"),
        leg("HU7301", operating="", reg="B-2222"),
    ])
    result = resolve(accepted, {}, [])
    assert result["hints"] == [] and result["totals"]["unverified_legs"] == 2


def test_aircraft_reg_conflict_quarantined_but_sortie_kept():
    # 两个营销号共享同一执飞架次，但机号互相矛盾 → 保留架次并隔离冲突证据
    accepted, _ = parse_legs([
        leg("CA7001", operating="", reg="B-1111"),
        leg("MU5101", "MU5101", reg="B-2222"),
    ])
    mappings, _ = parse_codeshares([
        {"marketing_flight_no": "CA7001", "operating_flight_no": "MU5101",
         "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
    ], accepted)
    result = resolve(accepted, mappings, [])
    assert result["totals"]["sorties"] == 1
    assert result["quarantine"][0]["reason_code"] == "AIRCRAFT_REG_CONFLICT"


def test_leg_conservation_balances():
    accepted, leg_quarantine = parse_legs([
        leg("MU5101", "MU5101"),
        leg("CZ3002", operating=""),
        leg("BAD", "", departure=""),
    ])
    result = resolve(accepted, {}, [])
    result["totals"]["quarantined_legs"] += len(leg_quarantine)
    assert result["totals"]["balanced"] is True


def test_fee_schedule_uses_airline_prefix_and_decimal():
    accepted, _ = parse_legs([leg("MU5101", "MU5101"), leg("CA1501", "CA1501")])
    result = resolve(accepted, {}, [{"airline": "MU", "fee": "2000.00"}])
    fees = {s["operating_flight_no"]: s["fee"] for s in result["sorties"]}
    assert fees["MU5101"] == "2000.00" and fees["CA1501"] == service.DEFAULT_FEE
    assert result["totals"]["total_fee"] == "3200.00"


# ---------- 批次持久化、幂等与重跑 ----------

def _payload(**overrides):
    payload = {
        "legs": [
            leg("CA7001", operating=""),
            leg("MU5101", "MU5101"),
            leg("MU5101", "MU5101", nature="EXTRA"),
            leg("CZ3002", operating="", dest="CAN", departure="2026-11-16T14:20"),
            leg("HU7301", operating="", dest="CAN", departure="2026-11-16T14:20"),
        ],
        "codeshares": [
            {"marketing_flight_no": "CA7001", "operating_flight_no": "MU5101",
             "flight_date": "2026-11-16", "origin": "PVG", "destination": "PEK"},
        ],
        "fee_schedule": [],
    }
    payload.update(overrides)
    return payload


def test_run_id_stable_and_rerun_does_not_double_charge(db):
    payload = _payload()
    first = service.create_run(payload)
    second = service.create_run(payload)
    assert first["run_id"] == second["run_id"]
    assert first["created"] is True and second["created"] is False and second["reused"] is True
    assert first["totals"]["charged_sorties"] == 2
    assert store.connect().execute(
        "SELECT COUNT(*) FROM billing_sorties WHERE charged=1").fetchone()[0] == 2


def test_codeshare_import_changes_sortie_count(db):
    without = service.create_run(_payload(codeshares=[]))
    # CA7001 无执飞号又无映射 → 未核实，只有 MU5101 正常+加班两个架次
    assert without["totals"]["sorties"] == 2
    with_map = service.create_run(_payload())
    # 映射补全后 CA7001 并入 MU5101，架次数仍为 2，但成员航班变了
    normal = next(s for s in with_map["sorties"] if s["flight_nature"] == "NORMAL")
    assert set(normal["flight_numbers"]) == {"CA7001", "MU5101"}
    unverified_nos = {l["flight_no"] for l in with_map["unverified"]}
    assert "CA7001" not in unverified_nos


def test_corrected_rerun_never_double_charges(db):
    broken = service.create_run(_payload(legs=[leg("MU5101", operating="")], codeshares=[]))
    assert broken["totals"]["charged_sorties"] == 0
    fixed = service.create_run(_payload(legs=[leg("MU5101", "MU5101")], codeshares=[]))
    assert fixed["totals"]["charged_sorties"] == 1
    again = service.create_run(_payload(legs=[leg("MU5101", "MU5101"),
                                              leg("CA1501", "CA1501")], codeshares=[]))
    # MU5101 历史批次已计费，本批只收 CA1501 一次
    mu = next(s for s in again["sorties"] if s["operating_flight_no"] == "MU5101")
    assert mu["status"] == "ALREADY_BILLED" and mu["charged"] is False
    assert again["totals"]["charged_sorties"] == 1
    assert store.connect().execute(
        "SELECT COUNT(*) FROM billing_sorties WHERE charged=1").fetchone()[0] == 2


def test_unsettled_legs_queryable_and_cleared_after_fix(db):
    service.create_run(_payload(legs=[leg("MU5101", operating="")], codeshares=[]))
    unsettled = service.unsettled_legs()
    assert {u["flight_no"] for u in unsettled} == {"MU5101"}
    service.create_run(_payload(legs=[leg("MU5101", "MU5101")], codeshares=[]))
    assert service.unsettled_legs() == []


def test_quarantined_legs_remain_unsettled(db):
    service.create_run(_payload(legs=[leg("MU5101", "MU5101", departure="")], codeshares=[]))
    unsettled = service.unsettled_legs()
    assert len(unsettled) == 1 and unsettled[0]["reason_code"] == "EMPTY_VALUE"


def test_database_unique_index_blocks_second_charge(db):
    import sqlite3
    service.create_run(_payload())
    key = "|".join(("MU5101", "2026-11-16", "PVG", "PEK", "2026-11-16T09:05", "NORMAL"))
    # 同一物理架次键已由批次收费，再次插入 charged=1 必须被数据库拒绝
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO billing_sorties (run_id, sortie_id, sortie_key, charged, sortie_json)"
                " VALUES ('rogue', 'S-rogue', ?, 1, '{}')", (key,))
    # 未收费记录（charged=0）不受部分唯一索引限制，可重复留痕
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO billing_sorties (run_id, sortie_id, sortie_key, charged, sortie_json)"
            " VALUES ('rogue2', 'S-rogue2', ?, 0, '{}')", (key,))
        conn.commit()


# ---------- 对账单导出可追溯 ----------

def test_statement_csv_traces_every_sortie_to_original_flights(db):
    run = service.create_run(_payload())
    text = statement_csv(run)
    reader = list(csv.DictReader(io.StringIO(text)))
    charged = [row for row in reader if row["status"] == "CHARGED"]
    assert len(charged) == run["totals"]["charged_sorties"]
    normal = next(row for row in charged if row["flight_nature"] == "NORMAL")
    assert "CA7001" in normal["all_flight_numbers"] and "MU5101" in normal["all_flight_numbers"]
    assert normal["source_line_nos"] and normal["sortie_id"] and normal["fee"]


def test_statement_json_structure(db):
    run = service.create_run(_payload())
    data = statement_json(run)
    assert data["run_id"] == run["run_id"]
    assert len(data["sorties"]) == run["totals"]["sorties"]
    assert all("flight_numbers" in s and "source_line_nos" in s for s in data["sorties"])


# ---------- HTTP 接口 ----------

def test_api_create_run_and_get_unsettled(db):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/billing/runs", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["totals"]["sorties"] == 2
    detail = client.get(f"/api/billing/runs/{body['run_id']}").json()
    assert detail["run_id"] == body["run_id"]
    unsettled = client.get("/api/billing/unsettled").json()
    assert {u["flight_no"] for u in unsettled} == {"CZ3002", "HU7301"}
    csv_resp = client.get(f"/api/billing/export/statement.csv?run_id={body['run_id']}")
    assert csv_resp.status_code == 200 and "sortie_id" in csv_resp.text


def test_billing_page_accessible(db):
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).get("/billing")
    assert response.status_code == 200 and "panel" in response.text


def test_csv_upload_creates_run(db):
    from fastapi.testclient import TestClient
    from app.main import app
    legs_buf = io.StringIO()
    writer = csv.DictWriter(legs_buf, fieldnames=list(leg("X").keys()))
    writer.writeheader()
    writer.writerow(leg("MU5101", "MU5101"))
    response = TestClient(app).post(
        "/api/billing/runs/csv",
        files={"legs": ("legs.csv", legs_buf.getvalue(), "text/csv")})
    assert response.status_code == 201 and response.json()["totals"]["charged_sorties"] == 1
