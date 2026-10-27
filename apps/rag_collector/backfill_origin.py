#!/usr/bin/env python3
"""
backfill_origin.py
==================

Fill column G (`author_is_african`) for rows that already have an author but no
verdict - the rows collected before the column existed.

    python backfill_origin.py --dry-run --limit 25   # preview, writes nothing
    python backfill_origin.py                       # judge every unjudged row

Only rows with a non-empty column F are considered: with no author there is
nothing to judge. Only blank G cells are written, so a verdict you corrected by
hand is never overwritten, and before each write column C is re-read so a sorted
or deleted sheet cannot misattribute a verdict. Safe to run while the collector
is appending rows.

Verdicts are cached per author, so the cost is one small model call per distinct
author, not per row. The cache persists in `origin_cache.json`, so a re-run (the
hourly timer runs this before the PDF downloader) only asks about authors it has
never judged; `--fresh` ignores the file and asks everyone again.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from author_origin import AfricanAuthorClassifier
from dork_sheet_collector import (
    BASE_DIR,
    SHEET_ID_DEFAULT,
    WORKSHEET_DEFAULT,
    SheetsManager,
    configure_logging,
    discover_credentials,
)

LOGGER = logging.getLogger("dork_collector.origin_backfill")

URL_COL, TITLE_COL, SOURCE_COL = 3, 4, 2   # C, D, B
AUTHOR_COL, ORIGIN_COL = 6, 7              # F, G
WRITE_CHUNK = 25


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--credentials", default=None)
    parser.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    parser.add_argument("--worksheet", default=WORKSHEET_DEFAULT)
    parser.add_argument("--dry-run", action="store_true", help="Judge but never write.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--cache",
        default=str(BASE_DIR / "origin_cache.json"),
        help="Verdicts kept across runs, including 'unsure' (the sheet leaves those blank).",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="Ignore the cache and judge every author again."
    )
    parser.add_argument(
        "--report",
        default=str(BASE_DIR / "logs" / "origin_backfill.csv"),
        help="CSV of every row judged: row, author, verdict, why.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, BASE_DIR / "logs" / "origin_backfill.log")

    cache_path = Path(args.cache)
    if args.fresh and cache_path.exists():
        cache_path.rename(cache_path.with_suffix(".json.old"))
    classifier = AfricanAuthorClassifier(cache_path=cache_path)
    if not classifier.enabled:
        LOGGER.error(
            "No LLM backend: set AWS credentials (Bedrock) or OPENAI_API_KEY; nothing to do"
        )
        return 2
    LOGGER.info("LLM: %s", classifier.llm.describe())

    sheets = SheetsManager(
        credentials_path=discover_credentials(args.credentials),
        sheet_id=args.sheet_id,
        worksheet_name=args.worksheet,
        dry_run=args.dry_run,
    )
    ws = sheets.worksheet
    rows = sheets._with_retry("read sheet", ws.get_all_values)

    def cell(row: list[str], column: int) -> str:
        return row[column - 1].strip() if len(row) >= column else ""

    unjudged = [
        (number, cell(row, URL_COL), cell(row, AUTHOR_COL),
         cell(row, TITLE_COL), cell(row, SOURCE_COL))
        for number, row in enumerate(rows[1:], start=2)
        if cell(row, AUTHOR_COL) and not cell(row, ORIGIN_COL)
    ]
    # A blank G whose author is already in the cache was judged "unsure" by an
    # earlier run; asking again would get the same answer.
    todo = [t for t in unjudged if not classifier.is_judged(t[2])]
    if args.limit is not None:
        todo = todo[: args.limit]

    LOGGER.info(
        "%d row(s) have an author but no verdict; %d already judged unsure, "
        "%d to judge%s",
        len(unjudged), len(unjudged) - len(todo) if args.limit is None else 0,
        len(todo), " [dry-run]" if args.dry_run else "",
    )
    if not todo:
        return 0

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"TRUE": 0, "FALSE": 0, "unsure": 0}
    pending: list[tuple[int, str, str]] = []   # (row, url, verdict)

    def write(batch: list[tuple[int, str, str]]) -> None:
        if not batch or args.dry_run:
            return
        urls = sheets._with_retry("re-read column C", lambda: ws.col_values(URL_COL))
        updates = [
            {"range": f"G{row}", "values": [[verdict]]}
            for row, url, verdict in batch
            if row <= len(urls) and urls[row - 1].strip() == url
        ]
        skipped = len(batch) - len(updates)
        if skipped:
            LOGGER.warning("%d row(s) moved since the read; skipped them", skipped)
        if updates:
            sheets._with_retry(
                f"write {len(updates)} verdict(s)",
                lambda: ws.batch_update(updates, value_input_option="RAW"),
            )
            LOGGER.info("Wrote %d verdict(s)", len(updates))

    with open(report_path, "w", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(
        max(1, args.workers)
    ) as pool:
        report = csv.writer(fh)
        report.writerow(["row", "author", "verdict", "why", "title"])
        futures = {
            pool.submit(classifier.classify, authors, title, source, url): (row, url, authors)
            for row, url, authors, title, source in todo
        }
        for done, future in enumerate(as_completed(futures), start=1):
            row, url, authors = futures[future]
            verdict = future.result()          # classify() never raises
            report.writerow([row, authors, verdict.value or "UNKNOWN", verdict.why, ""])
            counts[verdict.value if verdict else "unsure"] += 1
            # Only a decided verdict is worth a cell; unsure stays blank so a
            # later, better-informed run can still fill it.
            if verdict:
                pending.append((row, url, verdict.value))
            LOGGER.info(
                "[%d/%d] row %d %-8s %s",
                done, len(todo), row, verdict.value or "unsure", authors[:50],
            )
            if len(pending) >= WRITE_CHUNK:
                write(pending)
                pending = []
                if not args.dry_run:
                    classifier.save_cache()
        write(pending)
    if not args.dry_run:
        classifier.save_cache()

    LOGGER.info(
        "Done: African %d, not African %d, unsure %d (of %d) | %d model call(s), "
        "%d failed (asked again next run) | report %s",
        counts["TRUE"], counts["FALSE"], counts["unsure"], len(todo),
        classifier.calls, classifier.failures, report_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
