"""
CRRA Lab C2 - Mock Contract Management API

Serves the contract portfolio in data/contracts.csv with four derived policy
fields, so the Analysis Agent in Lab C3 gets repeatable results.

Run from the project root:
    python mcp_server/contract_shim.py
Then open http://localhost:5001/health in a browser.
"""

import csv
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "contracts.csv"

# Every run is analysed against the same "today" so results never drift.
SIMULATED_TODAY = date(2025, 4, 1)

INT_FIELDS = ("annual_value_inr", "notice_days", "seats_purchased",
              "seats_active", "proposed_uplift_pct")


def enrich(row: dict) -> dict:
    """Convert types and add notice_deadline, notice_state, utilisation_pct, approval_band."""
    for key in INT_FIELDS:
        row[key] = int(row[key])
    row["auto_renew"] = row.get("auto_renew", "").strip().upper() == "Y"

    renewal = datetime.strptime(row["renewal_date"], "%Y-%m-%d").date()
    deadline = renewal - timedelta(days=row["notice_days"])
    row["notice_deadline"] = deadline.isoformat()
    row["days_to_renewal"] = (renewal - SIMULATED_TODAY).days
    row["days_to_notice_deadline"] = (deadline - SIMULATED_TODAY).days

    if renewal < SIMULATED_TODAY:
        row["notice_state"] = "EXPIRED"
    elif deadline < SIMULATED_TODAY:
        row["notice_state"] = "INSIDE_WINDOW"
    elif row["days_to_notice_deadline"] <= 30:
        row["notice_state"] = "APPROACHING"
    else:
        row["notice_state"] = "OPEN"

    # AMC and support contracts have no seats, so utilisation is undefined
    if row["seats_purchased"] > 0:
        row["utilisation_pct"] = round(100 * row["seats_active"] / row["seats_purchased"])
    else:
        row["utilisation_pct"] = None

    value = row["annual_value_inr"]
    row["approval_band"] = "A" if value < 1_000_000 else ("B" if value <= 5_000_000 else "C")
    return row


def load_contracts() -> dict[str, dict]:
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return {r["contract_id"].strip().upper(): enrich(r) for r in csv.DictReader(f)}


# Loaded at import time, so `flask run` and tests see the data too,
# not only `python contract_shim.py`.
CONTRACTS = load_contracts()

app = Flask(__name__)


def not_found(contract_id: str):
    return jsonify({"error": f"Contract {contract_id} not found"}), 404


@app.get("/health")
def health():
    return jsonify({"status": "ok", "contracts_loaded": len(CONTRACTS),
                    "simulated_today": SIMULATED_TODAY.isoformat()})


@app.get("/api/contracts")
def list_contracts():
    """All contracts, with optional ?category= ?band= ?notice_state= filters."""
    results = list(CONTRACTS.values())
    filters = {"category": "category", "band": "approval_band", "notice_state": "notice_state"}
    for param, field in filters.items():
        wanted = request.args.get(param)
        if wanted:
            results = [c for c in results if c[field].lower() == wanted.lower()]
    return jsonify({"count": len(results), "contracts": results})


# Registered before /<contract_id>; Werkzeug also prefers static segments.
@app.get("/api/contracts/expiring")
def expiring():
    """Contracts renewing within ?days= (default 90) of the simulated today, soonest first."""
    days = request.args.get("days", 90, type=int)
    results = sorted((c for c in CONTRACTS.values() if 0 <= c["days_to_renewal"] <= days),
                     key=lambda c: c["days_to_renewal"])
    return jsonify({"count": len(results), "window_days": days, "contracts": results})


@app.get("/api/contracts/<contract_id>")
def get_contract(contract_id):
    contract = CONTRACTS.get(contract_id.strip().upper())
    return jsonify(contract) if contract else not_found(contract_id)


@app.get("/api/categories")
def categories():
    """Vendors grouped by category with total annual value, for overlap analysis."""
    grouped: dict[str, list[dict]] = {}
    for c in CONTRACTS.values():
        grouped.setdefault(c["category"], []).append(
            {"contract_id": c["contract_id"], "vendor": c["vendor"],
             "annual_value_inr": c["annual_value_inr"], "utilisation_pct": c["utilisation_pct"]})
    summary = [{"category": cat, "vendor_count": len(vendors),
                "total_annual_value_inr": sum(v["annual_value_inr"] for v in vendors),
                "vendors": vendors}
               for cat, vendors in sorted(grouped.items())]
    return jsonify({"count": len(summary), "categories": summary})


@app.patch("/api/contracts/<contract_id>")
def update_contract(contract_id):
    """Updates status, owner or proposed_uplift_pct in memory; a restart resets them."""
    contract = CONTRACTS.get(contract_id.strip().upper())
    if not contract:
        return not_found(contract_id)
    payload = request.get_json(silent=True) or {}
    changes = {k: payload[k] for k in ("status", "owner", "proposed_uplift_pct") if k in payload}
    if not changes:
        return jsonify({"error": "Send JSON with status, owner or proposed_uplift_pct"}), 400
    contract.update(changes)
    return jsonify({"updated": sorted(changes), "contract": contract})


if __name__ == "__main__":
    print(f"Mock Contract API: {len(CONTRACTS)} contracts from {CSV_PATH.name}, "
          f"simulated today {SIMULATED_TODAY}")
    print("http://localhost:5001/health")
    app.run(port=5001, debug=False)
