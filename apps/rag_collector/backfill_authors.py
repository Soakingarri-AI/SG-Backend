#!/usr/bin/env python3
"""
backfill_authors.py
===================

Fill column F ("Author(s)") for rows the collector wrote before author lookup
existed, or rows where lookup found nothing and you want to try again.

    python backfill_authors.py --dry-run --limit 20   # preview, writes nothing
    python backfill_authors.py                        # fill every blank F cell
    python backfill_authors.py --no-llm               # metadata sources only

Safe to run while the collector service is appending rows: the collector only
ever adds rows at the bottom, and before each write this script re-reads
column C and skips any row whose URL no longer matches (i.e. someone sorted or
deleted rows in the meantime). Only blank F cells are ever written, so a cell
someone corrected by hand is never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from author_resolver import AuthorResolver
from dork_sheet_collector import (
    BASE_DIR,
    SHEET_ID_DEFAULT,
    WORKSHEET_DEFAULT,
    SheetsManager,
    configure_logging,
    discover_credentials,
)

LOGGER = logging.getLogger("dork_collector.backfill")

AUTHOR_COL = 6          # F
WRITE_CHUNK = 25        # rows per batch_update call


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--credentials", default=None)
    parser.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    parser.add_argument("--worksheet", default=WORKSHEET_DEFAULT)
    parser.add_argument("--dry-run", action="store_true", help="Resolve but never write.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-llm", action="store_true", help="Disable the LLM fallback.")
    parser.add_argument(
        "--report",
        default=str(BASE_DIR / "logs" / "author_backfill.csv"),
        help="CSV of every row processed: row, url, method, authors.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, BASE_DIR / "logs" / "author_backfill.log")

    sheets = SheetsManager(
        credentials_path=discover_credentials(args.credentials),
        sheet_id=args.sheet_id,
        worksheet_name=args.worksheet,
        dry_run=args.dry_run,
    )
    ws = sheets.worksheet
    rows = sheets._with_retry("read sheet", ws.get_all_values)

    # (sheet row number, url, title) for every row with a URL and a blank F.
    todo = [
        (number, row[2].strip(), row[3].strip() if len(row) > 3 else "")
        for number, row in enumerate(rows[1:], start=2)
        if len(row) > 2
        and row[2].strip().lower().startswith(("http://", "https://"))
        and not (len(row) >= AUTHOR_COL and row[AUTHOR_COL - 1].strip())
    ]
    if args.limit is not None:
        todo = todo[: args.limit]

    resolver = AuthorResolver(use_llm=False if args.no_llm else None)
    LOGGER.info(
        "%d row(s) need authors (LLM fallback %s)%s",
        len(todo),
        "on" if resolver.use_llm else "off",
        " [dry-run]" if args.dry_run else "",
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    found = 0
    pending: list[tuple[int, str, str]] = []  # (row, url, authors) awaiting write

    def write(batch: list[tuple[int, str, str]]) -> None:
        if not batch or args.dry_run:
            return
        # Re-read column C so a sorted/deleted sheet can't misattribute authors.
        urls = sheets._with_retry("re-read column C", lambda: ws.col_values(3))
        updates = [
            {"range": f"F{row}", "values": [[authors]]}
            for row, url, authors in batch
            if row <= len(urls) and urls[row - 1].strip() == url
        ]
        skipped = len(batch) - len(updates)
        if skipped:
            LOGGER.warning("%d row(s) moved since the read; skipped them", skipped)
        if updates:
            sheets._with_retry(
                f"write {len(updates)} author cell(s)",
                lambda: ws.batch_update(updates, value_input_option="RAW"),
            )
            LOGGER.info("Wrote %d author cell(s)", len(updates))

    with open(report_path, "w", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(
        max(1, args.workers)
    ) as pool:
        report = csv.writer(fh)
        report.writerow(["row", "url", "title", "method", "authors"])
        futures = {
            pool.submit(resolver.resolve, url, title): (row, url, title)
            for row, url, title in todo
        }
        for done, future in enumerate(as_completed(futures), start=1):
            row, url, title = futures[future]
            result = future.result()  # resolve() never raises
            report.writerow([row, url, title, result.method, result.text])
            if result:
                found += 1
                pending.append((row, url, result.text))
            LOGGER.info(
                "[%d/%d] row %d %-15s %s",
                done, len(todo), row, result.method or "-", result.text[:70] or "(none)",
            )
            if len(pending) >= WRITE_CHUNK:
                write(pending)
                pending = []
        write(pending)

    LOGGER.info(
        "Done: authors found for %d/%d row(s). Report: %s", found, len(todo), report_path
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
