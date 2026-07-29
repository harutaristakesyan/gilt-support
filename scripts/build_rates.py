#!/usr/bin/env python3
"""Build rates/latest.json — the daily exchange-rate snapshot the Gilt app reads.

Why a published file instead of every device calling the provider itself: two
members of one household fetching minutes apart used to get slightly different
tables, so the same shared ledger showed 355 on one phone and 356 on the other.
One file per UTC day makes every device's conversion identical by construction.

Shape (decoded by `RatesClient.RateSnapshot` in the app):

    {"base": "EUR", "asOf": "2026-07-29", "rates": {"USD": 1.0842, ...}}

`asOf` is the ECB reference date the provider reports, not the run date — it
lags on weekends and holidays, and the app's staleness window allows for that.

Rates are read and written as exact decimal text: routing them through a float
would turn 1.0842 into 1.0842000000000002 and reintroduce the rounding
differences this file exists to remove.
"""
from __future__ import annotations

import decimal
import json
import pathlib
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

PROVIDER = "https://api.frankfurter.dev/v2/rates?base=EUR"
USER_AGENT = "gilt-rates-publisher (+https://gilt.money)"
BASE = "EUR"
OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "rates" / "latest.json"

# The app stops trusting a snapshot at 72h; failing at 48 means a wedged
# publisher is a red run before it is ever a user-visible fallback.
MAX_AGE = timedelta(hours=48)


def build() -> dict:
    # A named User-Agent: the provider's edge rejects urllib's default with a 403,
    # and an identifiable one is the courteous thing to send a free API anyway.
    request = urllib.request.Request(PROVIDER, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            sys.exit(f"provider returned HTTP {response.status}")
        body = response.read().decode("utf-8")
    rows = json.loads(body, parse_float=decimal.Decimal)
    if not rows:
        sys.exit("provider returned no rates")

    bases = {row["base"] for row in rows}
    if bases != {BASE}:
        sys.exit(f"expected every row on base {BASE}, got {sorted(bases)}")

    # One reference date for the whole table: the provider dates each row
    # separately, and a mixed-date snapshot is not the single fact we publish.
    as_of = max(row["date"] for row in rows)
    rates = {row["quote"]: row["rate"] for row in rows if row["quote"] != BASE}
    return {"base": BASE, "asOf": as_of, "rates": dict(sorted(rates.items()))}


def check_freshness(as_of: str) -> None:
    try:
        reference = datetime.strptime(as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        sys.exit(f"provider reference date is not yyyy-MM-dd: {as_of!r}")
    age = datetime.now(timezone.utc) - reference
    if age > MAX_AGE:
        sys.exit(f"provider reference date {as_of} is {age.days}d old — refusing to publish")


def render(snapshot: dict) -> str:
    """Hand-rolled because `json.dumps` has no `Decimal` support: `default=str`
    would quote the rates, and the app decodes them as numbers."""
    entries = list(snapshot["rates"].items())
    lines = ["{",
             f'  "base": "{snapshot["base"]}",',
             f'  "asOf": "{snapshot["asOf"]}",',
             '  "rates": {']
    for index, (code, rate) in enumerate(entries):
        comma = "," if index < len(entries) - 1 else ""
        lines.append(f'    "{code}": {rate}{comma}')
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    snapshot = build()
    check_freshness(snapshot["asOf"])

    rendered = render(snapshot)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Idempotent: a second run on the same UTC day writes nothing, so the
    # workflow has nothing to commit and the published bytes stay stable.
    if OUTPUT.exists() and OUTPUT.read_text() == rendered:
        print(f"unchanged — already published {snapshot['asOf']}")
        return
    OUTPUT.write_text(rendered)
    print(f"wrote {OUTPUT} — asOf {snapshot['asOf']}, {len(snapshot['rates'])} currencies")


if __name__ == "__main__":
    main()
