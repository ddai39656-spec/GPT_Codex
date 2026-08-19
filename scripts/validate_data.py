"""Fail the update workflow when generated data could mislead the terminal."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def load(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2:
        raise SystemExit(f"{path}: unsupported schema")
    timestamp = datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age < -60 or age > 3600:
        raise SystemExit(f"{path}: timestamp is not fresh")
    return payload


def main() -> None:
    market = load("data/market.json")
    crypto = load("data/crypto.json")
    if not market.get("us"):
        raise SystemExit("market.json: empty stock universe")
    for candidate in crypto.get("candidates") or []:
        if candidate.get("actionable"):
            critical = [item for item in candidate.get("checks") or [] if item.get("critical")]
            if not critical or any(item.get("status") != "pass" for item in critical):
                raise SystemExit(f"unsafe actionable candidate: {candidate.get('id')}")
            if any(value is None for value in (candidate.get("ca"), candidate.get("pool"))):
                raise SystemExit(f"actionable candidate missing identity: {candidate.get('id')}")
    print(f"validated: {len(market['us'])} stocks, {len(crypto.get('candidates') or [])} crypto pools")


if __name__ == "__main__":
    main()

