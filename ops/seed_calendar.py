"""Seed trading_calendar for whole years from an explicit list of weekday market holidays.

HOSE closes on public holidays AND on the government's compensatory/swapped days off (e.g. 02/05/2025,
02/01/2026), so each year's list is the official day-off schedule, not just the holidays in the Labour
Code. 2024-2026 are inferred from vnstock bars (weekdays without a VNINDEX/VNM/FPT bar); 2027 is from
VPCP 10065/VPCP-KGVX (02/10/2026). Add next year's list when it is announced (usually October) — the
scheduler's freshness check alerts 30 days before the calendar runs out.

    python -m ops.seed_calendar 2027 [2028 ...]
"""
from __future__ import annotations

import sys
from datetime import date

from mcp_server.connection import get_rw_conn
from pipeline.calendar import seed_calendar_from_weekdays

HOLIDAYS: dict[int, list[str]] = {
    2024: ["01-01", "02-08", "02-09", "02-12", "02-13", "02-14", "04-18", "04-29", "04-30", "05-01",
           "09-02", "09-03"],
    2025: ["01-01", "01-27", "01-28", "01-29", "01-30", "01-31", "04-07", "04-30", "05-01", "05-02",
           "09-01", "09-02"],
    2026: ["01-01", "01-02", "02-16", "02-17", "02-18", "02-19", "02-20", "04-27", "04-30", "05-01",
           "08-31", "09-01", "09-02", "11-24"],  # 24/11: Ngày Văn hóa Việt Nam (NQ 28/2026/QH16), no swap
    2027: ["01-01", "02-04", "02-05", "02-08", "02-09", "02-10", "04-16", "04-30", "05-03",
           "09-02", "09-03", "11-24"],
}


def holidays_of(year: int) -> set[date]:
    return {date.fromisoformat(f"{year}-{md}") for md in HOLIDAYS[year]}


def main(years: list[int]) -> None:
    with get_rw_conn() as conn:
        for year in years:
            n = seed_calendar_from_weekdays(conn, date(year, 1, 1), date(year, 12, 31), holidays_of(year))
            print(f"{year}: {n} ngày, {len(HOLIDAYS[year])} ngày nghỉ")


if __name__ == "__main__":
    main([int(y) for y in sys.argv[1:]])
