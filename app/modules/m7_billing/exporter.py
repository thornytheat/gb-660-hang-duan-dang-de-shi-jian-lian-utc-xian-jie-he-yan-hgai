"""对账单导出：每个计费架次都带全部原航班号与物理行号，可逐架次回溯。"""
import csv
import io

_COLUMNS = [
    "run_id", "sortie_id", "status", "operating_flight_no", "flight_date",
    "origin", "destination", "actual_departure", "flight_nature", "aircraft_reg",
    "all_flight_numbers", "source_line_nos", "fee",
]


def _rows(run: dict) -> list[dict]:
    rows = []
    for sortie in run["sorties"]:
        rows.append({
            "run_id": run["run_id"],
            "sortie_id": sortie["sortie_id"],
            "status": sortie["status"],
            "operating_flight_no": sortie["operating_flight_no"],
            "flight_date": sortie["flight_date"],
            "origin": sortie["origin"],
            "destination": sortie["destination"],
            "actual_departure": sortie["actual_departure"],
            "flight_nature": sortie["flight_nature"],
            "aircraft_reg": sortie["aircraft_reg"],
            "all_flight_numbers": " ".join(sortie["flight_numbers"]),
            "source_line_nos": " ".join(str(n) for n in sortie["source_line_nos"]),
            "fee": sortie["fee"],
        })
    return rows


def statement_csv(run: dict) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_COLUMNS)
    writer.writeheader()
    writer.writerows(_rows(run))
    return output.getvalue()


def statement_json(run: dict) -> dict:
    return {
        "run_id": run["run_id"],
        "rule_version": run["rule_version"],
        "input_sha256": run["input_sha256"],
        "totals": run["totals"],
        "sorties": [
            {key: sortie[key] for key in (
                "sortie_id", "status", "charged", "operating_flight_no", "flight_date",
                "origin", "destination", "actual_departure", "flight_nature",
                "aircraft_reg", "flight_numbers", "source_line_nos", "fee")}
            for sortie in run["sorties"]
        ],
        "unverified": run["unverified"],
        "hints": run["hints"],
    }
