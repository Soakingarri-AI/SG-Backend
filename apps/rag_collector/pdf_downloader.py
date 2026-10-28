#!/usr/bin/env python3
"""
pdf_downloader.py
=================

Copy the PDF behind every row whose author is African into S3, the raw corpus
for the Ask SoakinGarri RAG index.

    python pdf_downloader.py --dry-run --limit 10   # resolve + download, write nothing
    python pdf_downloader.py                        # every pending row
    python pdf_downloader.py --retry-failed         # also re-try rows marked failed

Sheet contract
--------------
    G  author_is_african   read: only TRUE rows are downloaded
    H  downloaded          TRUE once the PDF is in S3. The collector writes FALSE
                           on arrival, so FALSE means "not downloaded (yet)".
    I  download_note       the outcome:
                             s3://bucket/key     downloaded
                             failed: <reason>    definite (404, login wall, no PDF
                                                 on the page); skipped by later
                                                 runs unless --retry-failed
                             retry: <reason>     temporary (timeout, 5xx); the
                                                 next run tries again

Before each write, column C is re-read and a row is only written if its URL is
unchanged, so sorting or deleting rows mid-run cannot misattribute a result.
Safe to run while the collector is appending rows.

Finding the PDF
---------------
About half the rows link to a landing page, not the file. In order:
  1. the URL itself, if it serves a PDF;
  2. archive.org items: the item's PDF, via the metadata API;
  3. the page's own pointers: citation_pdf_url and similar meta tags, OJS
     galley links (article/view/A/B -> article/download/A/B), then <a> links
     that look like PDFs.
Every candidate must actually start with %PDF; a login page served as
"application/pdf" is rejected. A PDF found through a plain <a> link must also
carry the row's title on its first pages: landing pages link other PDFs too
(author guidelines, issue covers), and a wrong document is worse than none.

S3 layout
---------
    pdfs/<title-slug>-<url-hash>.pdf
    pdfs/<title-slug>-<url-hash>.pdf.metadata.json

The sidecar is in the shape Amazon Bedrock Knowledge Bases reads
(`metadataAttributes`), so the bucket can be indexed as-is. Keys depend only on
the URL, so a re-run finds an object it already uploaded and just marks the row.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

from dork_sheet_collector import (
    BASE_DIR,
    SHEET_ID_DEFAULT,
    WORKSHEET_DEFAULT,
    SheetsManager,
    configure_logging,
    discover_credentials,
    normalise_url,
)

LOGGER = logging.getLogger("dork_collector.pdf_downloader")

BUCKET_DEFAULT = os.getenv("RAG_BUCKET", "soakingarri-rag-corpus")
PREFIX = "pdfs/"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Columns (1-based).
CONCEPT_COL, SOURCE_COL, URL_COL, TITLE_COL = 1, 2, 3, 4
OPEN_COL, AUTHOR_COL, AFRICAN_COL, DOWNLOADED_COL, NOTE_COL = 5, 6, 7, 8, 9
NOTE_HEADER = "download_note"
WRITE_CHUNK = 20

PDF_MAX_BYTES = int(float(os.getenv("DOWNLOAD_PDF_MAX_MB", "150")) * 1024 * 1024)
# The Ask Knowledge Base indexing this bucket (infra/bedrock_kb/setup_kb.py
# prints both ids). Unset = do not sync after uploading.
KB_ID = os.getenv("BEDROCK_KB_ID", "")
KB_DATA_SOURCE_ID = os.getenv("BEDROCK_KB_DATA_SOURCE_ID", "")
SIDECAR_MAX_BYTES = 1000         # the Knowledge Base's limit is 1024
HTML_MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = (15, 60)               # (connect, read) seconds
MAX_CANDIDATES = 6               # PDF links tried per landing page
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en,fr;q=0.8",
}

# Meta tags publishers use to point at the full-text PDF.
PDF_META_KEYS = (
    "citation_pdf_url",
    "bepress_citation_pdf_url",
    "eprints.document_url",
    "fulltext_pdf_url",
)
_HREF_RE = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", re.I | re.S)
_ALT_PDF_RE = re.compile(
    r"""<link\b[^>]*type\s*=\s*["']application/pdf["'][^>]*href\s*=\s*["']([^"']+)["']""", re.I
)
_OJS_VIEW_RE = re.compile(r"/article/view/(\d+)/(\d+)")
# Interstitials that stand in for the real page. These are not bypassed; the
# row is recorded as blocked so the reason in column I is the true one.
_BOT_WALL_RE = re.compile(
    r"<title>\s*(?:bot verification|just a moment|attention required|access denied)"
    r"|recaptcha|hcaptcha|cf-challenge|challenge-platform",
    re.I,
)
_ARCHIVE_ID_RE = re.compile(r"/(?:details|download|stream|embed)/([^/?#]+)")


# --------------------------------------------------------------------------- #
#  Outcomes
# --------------------------------------------------------------------------- #

class Failed(Exception):
    """Definitive: retrying will not help."""


class Retry(Exception):
    """Temporary: the next run should try again."""


@dataclass
class Row:
    number: int
    url: str
    title: str
    author: str
    concept: str
    source: str
    open_access: bool


@dataclass
class Outcome:
    row: Row
    downloaded: bool
    note: str
    size: int = 0


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def slugify(text: str, limit: int = 80) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "document"


def object_key(row: Row) -> str:
    digest = hashlib.sha1(normalise_url(row.url).encode()).hexdigest()[:10]
    return f"{PREFIX}{slugify(row.title)}-{digest}.pdf"


def meta_values(html: str, key: str) -> list[str]:
    key = re.escape(key)
    values: list[str] = []
    for pattern in (
        rf'<meta[^>]+(?:name|property)=["\']{key}["\'][^>]*content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']{key}["\']',
    ):
        values += re.findall(pattern, html, re.I)
    return values


def pdf_candidates(html: str, base_url: str) -> list[tuple[str, bool]]:
    """
    Links on a landing page that may lead to its PDF, best first, each paired
    with whether it is *trusted*. Publisher meta tags and OJS galleys point at
    this document's file; a bare <a href="....pdf"> may be the journal's author
    guidelines, so the caller checks those against the title.
    """
    trusted: list[str] = []
    for key in PDF_META_KEYS:
        trusted += meta_values(html, key)
    trusted += _ALT_PDF_RE.findall(html)

    links = [(href, re.sub(r"<[^>]+>", " ", text).lower()) for href, text in _HREF_RE.findall(html)]
    # OJS: the galley "view" page wraps the file in a viewer; "download" serves it.
    for href, _ in links:
        match = _OJS_VIEW_RE.search(href)
        if match:
            trusted.append(href[: match.start()] + f"/article/download/{match[1]}/{match[2]}")
        elif "/article/download/" in href:
            trusted.append(href)
    generic = [href for href, _ in links if urlparse(href).path.lower().endswith(".pdf")]
    generic += [
        href for href, text in links
        if "pdf" in text and ("download" in href.lower() or "pdf" in href.lower())
    ]

    resolved: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for href, is_trusted in [(h, True) for h in trusted] + [(h, False) for h in generic]:
        url = urljoin(base_url, href.strip().replace("&amp;", "&"))
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            resolved.append((url, is_trusted))
    return resolved[:MAX_CANDIDATES]


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


_TITLE_STOPWORDS = frozenset(
    "with from that this their about into over under between among during africa "
    "african study analysis case review history".split()
)


def title_matches(path: Path, title: str) -> bool:
    """True when at least half the title's distinctive words are on the first pages."""
    words = set(re.findall(r"[a-z]{4,}", _fold(title))) - _TITLE_STOPWORDS
    if len(words) < 2:
        return True          # too short to judge either way
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = " ".join(
            reader.pages[i].extract_text() or "" for i in range(min(2, len(reader.pages)))
        )
    except Exception:
        return False
    folded = _fold(text)
    return sum(word in folded for word in words) / len(words) >= 0.5


# --------------------------------------------------------------------------- #
#  Downloader
# --------------------------------------------------------------------------- #

class PdfDownloader:
    """Thread-safe: one shared Session and S3 client, no per-call state."""

    def __init__(self, bucket: str, dry_run: bool = False) -> None:
        import boto3

        self.bucket = bucket
        self.dry_run = dry_run
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.s3 = boto3.client("s3", region_name=AWS_REGION)
        # One download at a time per host: parallel bursts at one site are what
        # trip bot challenges, and they are impolite besides.
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_locks_guard = threading.Lock()

    def _host_lock(self, url: str) -> threading.Lock:
        host = urlparse(url).netloc.lower()
        with self._host_locks_guard:
            return self._host_locks.setdefault(host, threading.Lock())

    # -- per row ------------------------------------------------------------- #

    def process(self, row: Row) -> Outcome:
        key = object_key(row)
        uri = f"s3://{self.bucket}/{key}"
        try:
            if not self.dry_run and self._exists(key):
                return Outcome(row, True, uri)      # uploaded by an earlier run
            with self._host_lock(row.url):
                path = self._fetch_pdf(row.url, row.title)
            try:
                size = path.stat().st_size
                if not self.dry_run:
                    self._upload(path, key, row)
            finally:
                path.unlink(missing_ok=True)
            return Outcome(row, True, uri, size)
        except Failed as exc:
            return Outcome(row, False, f"failed: {exc}")
        except Retry as exc:
            return Outcome(row, False, f"retry: {exc}")
        except Exception as exc:  # never let one row stop the run
            LOGGER.exception("Row %d crashed", row.number)
            return Outcome(row, False, f"retry: {type(exc).__name__}: {str(exc)[:120]}")

    # -- resolving ----------------------------------------------------------- #

    def _fetch_pdf(self, url: str, title: str) -> Path:
        if "archive.org" in urlparse(url).netloc and _ARCHIVE_ID_RE.search(urlparse(url).path):
            if not urlparse(url).path.lower().endswith(".pdf"):
                return self._fetch_archive_item(url)

        kind, payload, final_url = self._get(url)
        if kind == "pdf":
            return payload
        if len(payload) < 50_000 and _BOT_WALL_RE.search(payload):
            raise Failed("blocked by a bot check / CAPTCHA page")
        candidates = pdf_candidates(payload, final_url)
        if not candidates:
            raise Failed("no PDF link on the landing page")

        temporary: list[str] = []
        mismatched = 0
        for candidate, trusted in candidates:
            try:
                kind, result, _ = self._get(candidate)
            except Failed:
                continue
            except Retry as exc:
                temporary.append(str(exc))
                continue
            if kind != "pdf":
                continue
            if trusted or title_matches(result, title):
                return result
            result.unlink(missing_ok=True)       # some other PDF on the page
            mismatched += 1
        if temporary:
            raise Retry(f"PDF link(s) unreachable: {temporary[0]}")
        if mismatched:
            raise Failed(f"{mismatched} PDF(s) on the page, none matching the title")
        raise Failed(f"none of {len(candidates)} PDF link(s) on the page served a PDF")

    def _fetch_archive_item(self, url: str) -> Path:
        identifier = unquote(_ARCHIVE_ID_RE.search(urlparse(url).path)[1])
        try:
            response = self.session.get(
                f"https://archive.org/metadata/{quote(identifier)}", timeout=TIMEOUT
            )
            data = response.json() if response.status_code == 200 else {}
        except (requests.RequestException, ValueError) as exc:
            raise Retry(f"archive.org metadata: {exc}") from exc
        if not data:
            raise Failed("archive.org item not found")
        if data.get("is_dark"):
            raise Failed("archive.org item is dark (withdrawn)")

        pdfs = [f for f in data.get("files") or [] if str(f.get("name", "")).lower().endswith(".pdf")]
        if not pdfs:
            raise Failed("archive.org item has no PDF")
        wanted = unquote(urlparse(url).path)
        # The file the URL names, else the uploader's original, else the largest.
        pdfs.sort(
            key=lambda f: (
                f["name"] not in wanted,
                f.get("source") != "original",
                -int(f.get("size") or 0),
            )
        )
        name = pdfs[0]["name"]
        kind, payload, _ = self._get(
            f"https://archive.org/download/{quote(identifier)}/{quote(name)}"
        )
        if kind != "pdf":
            raise Failed("archive.org file is not a PDF")
        return payload

    def _get(self, url: str) -> tuple[str, Path | str, str]:
        """
        Fetch `url`. Returns ("pdf", temp_path, final_url) for a real PDF, or
        ("html", page_text, final_url) for anything else worth parsing.
        """
        try:
            response = self.session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
        except requests.exceptions.Timeout as exc:
            raise Retry("timed out") from exc
        except requests.exceptions.SSLError as exc:
            raise Failed("TLS error") from exc
        except requests.exceptions.ConnectionError as exc:
            raise Retry("connection error") from exc
        except requests.RequestException as exc:
            raise Failed(f"bad URL: {type(exc).__name__}") from exc

        with response:
            status = response.status_code
            if status in (404, 410):
                raise Failed(f"HTTP {status} (gone)")
            if status in (401, 403):
                raise Failed(f"HTTP {status} (blocked or login required)")
            # 202 is an AWS WAF bot challenge (AJOL serves one to this box's IP
            # some of the time). Not bypassed: retried, since it lifts later.
            if status == 202:
                raise Retry("HTTP 202 bot challenge; the site usually lifts it later")
            if status in (408, 429) or status >= 500:
                raise Retry(f"HTTP {status}")
            if status != 200:
                raise Failed(f"HTTP {status}")

            declared = int(response.headers.get("Content-Length") or 0)
            stream = response.iter_content(65536)
            head = b""
            for chunk in stream:
                head += chunk
                if len(head) >= 1024:
                    break
            if b"%PDF" in head[:1024]:
                if declared > PDF_MAX_BYTES:
                    raise Failed(f"PDF too large ({declared // 2**20} MB)")
                return "pdf", self._spool(head, stream), response.url

            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if "pdf" in ctype:
                # e.g. a 31-byte "Verification code is not valid." labelled as a PDF.
                raise Failed("labelled as a PDF but is not one (bot protection?)")
            if ctype and "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                raise Failed(f"not a PDF ({ctype})")
            text = head
            for chunk in stream:
                text += chunk
                if len(text) > HTML_MAX_BYTES:
                    break
            return "html", text.decode(response.encoding or "utf-8", errors="replace"), response.url

    def _spool(self, head: bytes, stream) -> Path:
        handle = tempfile.NamedTemporaryFile(prefix="rag-", suffix=".pdf", delete=False)
        total = len(head)
        try:
            with handle:
                handle.write(head)
                for chunk in stream:
                    total += len(chunk)
                    if total > PDF_MAX_BYTES:
                        raise Failed(f"PDF too large (>{PDF_MAX_BYTES // 2**20} MB)")
                    handle.write(chunk)
        except requests.RequestException as exc:
            Path(handle.name).unlink(missing_ok=True)
            raise Retry("download interrupted") from exc
        except Failed:
            Path(handle.name).unlink(missing_ok=True)
            raise
        return Path(handle.name)

    # -- S3 ------------------------------------------------------------------ #

    def _exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise Retry(f"S3 check failed: {exc}") from exc

    def _upload(self, path: Path, key: str, row: Row) -> None:
        sidecar = sidecar_json({
            "title": row.title,
            "author": row.author,
            "source_url": row.url,
            "host": urlparse(row.url).netloc,
            "concept": row.concept,
            "open_access": row.open_access,
            "author_is_african": True,
        })
        try:
            # Sidecar first: an object without its metadata would index badly,
            # while a sidecar without its PDF is simply ignored.
            self.s3.put_object(
                Bucket=self.bucket,
                Key=f"{key}.metadata.json",
                Body=sidecar,
                ContentType="application/json",
            )
            self.s3.upload_file(
                str(path),
                self.bucket,
                key,
                ExtraArgs={
                    "ContentType": "application/pdf",
                    # S3 user metadata must be ASCII.
                    "Metadata": {"source-url": quote(row.url, safe=":/?&=%")[:1500]},
                },
            )
        except Exception as exc:
            raise Retry(f"S3 upload failed: {exc}") from exc


# --------------------------------------------------------------------------- #
#  Sheet plumbing
# --------------------------------------------------------------------------- #

def cell(row: list[str], column: int) -> str:
    return row[column - 1].strip() if len(row) >= column else ""


def pending_rows(values: list[list[str]], retry_failed: bool) -> list[Row]:
    rows: list[Row] = []
    for number, raw in enumerate(values[1:], start=2):
        if cell(raw, AFRICAN_COL).upper() != "TRUE":
            continue
        if cell(raw, DOWNLOADED_COL).upper() == "TRUE":
            continue
        if cell(raw, NOTE_COL).startswith("failed:") and not retry_failed:
            continue
        url = cell(raw, URL_COL)
        if not url.lower().startswith(("http://", "https://")):
            continue
        rows.append(
            Row(
                number=number,
                url=url,
                title=cell(raw, TITLE_COL),
                author=cell(raw, AUTHOR_COL),
                concept=cell(raw, CONCEPT_COL),
                source=cell(raw, SOURCE_COL),
                open_access=cell(raw, OPEN_COL).upper() == "TRUE",
            )
        )
    return rows


def sidecar_json(attributes: dict) -> bytes:
    """The `.metadata.json` body, kept within SIDECAR_MAX_BYTES.

    The Knowledge Base skips a PDF whose metadata is over 1 KB (three were,
    each with a 600-character URL). Long titles and author lists are shortened;
    a URL that still does not fit is dropped rather than cut, since a truncated
    link points nowhere, and the title still identifies the document.
    """
    attrs = dict(attributes)
    attrs["title"] = str(attrs.get("title", ""))[:300]
    attrs["author"] = str(attrs.get("author", ""))[:200]

    def encode() -> bytes:
        return json.dumps({"metadataAttributes": attrs}, ensure_ascii=False).encode()

    body = encode()
    if len(body) > SIDECAR_MAX_BYTES:
        attrs.pop("source_url", None)
        body = encode()
    while len(body) > SIDECAR_MAX_BYTES and attrs["title"]:
        attrs["title"] = attrs["title"][:-20]
        body = encode()
    return body


def start_kb_sync() -> None:
    """Ask the Knowledge Base to index what this run uploaded.

    Ingestion is incremental (only new or changed PDFs are embedded) and runs
    on AWS, so this only starts the job. If last hour's job is still running
    the call is refused; the next run with uploads picks up what it missed.
    """
    if not (KB_ID and KB_DATA_SOURCE_ID):
        LOGGER.info("BEDROCK_KB_ID / BEDROCK_KB_DATA_SOURCE_ID unset; not syncing the Knowledge Base")
        return
    import boto3

    try:
        job = boto3.client(
            "bedrock-agent", region_name=os.getenv("BEDROCK_REGION", "us-east-1")
        ).start_ingestion_job(
            knowledgeBaseId=KB_ID, dataSourceId=KB_DATA_SOURCE_ID, description="pdf_downloader.py"
        )["ingestionJob"]
        LOGGER.info("Knowledge Base sync started: job %s", job["ingestionJobId"])
    except Exception as exc:
        LOGGER.warning("Knowledge Base sync not started: %s", exc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download African-author PDFs into S3.")
    parser.add_argument("--credentials", default=None)
    parser.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    parser.add_argument("--worksheet", default=WORKSHEET_DEFAULT)
    parser.add_argument("--bucket", default=BUCKET_DEFAULT)
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and download, but upload and write nothing.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most N rows.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retry-failed", action="store_true",
                        help="Also retry rows whose note says 'failed:'.")
    parser.add_argument(
        "--report",
        default=str(BASE_DIR / "logs" / "pdf_download.csv"),
        help="CSV of every row attempted: row, outcome, note, bytes, url.",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, BASE_DIR / "logs" / "pdf_downloader.log")

    try:
        import boto3

        if boto3.Session().get_credentials() is None:
            raise RuntimeError("no AWS credentials found")
    except Exception as exc:
        LOGGER.error(
            "Cannot reach S3 (%s). Locally set AWS_PROFILE=soakingarri; "
            "on the box the instance role provides credentials.", exc,
        )
        return 2

    sheets = SheetsManager(
        credentials_path=discover_credentials(args.credentials),
        sheet_id=args.sheet_id,
        worksheet_name=args.worksheet,
        dry_run=args.dry_run,
    )
    ws = sheets.worksheet
    values = sheets._with_retry("read sheet", ws.get_all_values)

    if not args.dry_run and cell(values[0], NOTE_COL) != NOTE_HEADER:
        sheets._with_retry(
            "write column I header",
            lambda: ws.update(values=[[NOTE_HEADER]], range_name="I1", value_input_option="RAW"),
        )

    todo = pending_rows(values, args.retry_failed)
    if args.limit is not None:
        todo = todo[: args.limit]
    LOGGER.info(
        "%d row(s) to download into s3://%s/%s%s",
        len(todo), args.bucket, PREFIX, " [dry-run]" if args.dry_run else "",
    )
    if not todo:
        return 0

    downloader = PdfDownloader(args.bucket, dry_run=args.dry_run)
    counts = {"downloaded": 0, "failed": 0, "retry": 0}
    total_bytes = 0
    pending: list[Outcome] = []

    def write(batch: list[Outcome]) -> None:
        if not batch or args.dry_run:
            return
        urls = sheets._with_retry("re-read column C", lambda: ws.col_values(URL_COL))
        updates = [
            {
                "range": f"H{o.row.number}:I{o.row.number}",
                "values": [["TRUE" if o.downloaded else "FALSE", o.note]],
            }
            for o in batch
            if o.row.number <= len(urls) and urls[o.row.number - 1].strip() == o.row.url
        ]
        skipped = len(batch) - len(updates)
        if skipped:
            LOGGER.warning("%d row(s) moved since the read; skipped them", skipped)
        if updates:
            # USER_ENTERED, like the collector, so H holds real booleans.
            sheets._with_retry(
                f"write {len(updates)} result(s)",
                lambda: ws.batch_update(updates, value_input_option="USER_ENTERED"),
            )
            LOGGER.info("Wrote %d result(s) to the sheet", len(updates))

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", newline="", encoding="utf-8") as fh, ThreadPoolExecutor(
        max(1, args.workers)
    ) as pool:
        report = csv.writer(fh)
        report.writerow(["row", "outcome", "note", "bytes", "url", "title"])
        futures = [pool.submit(downloader.process, row) for row in todo]
        for done, future in enumerate(as_completed(futures), start=1):
            outcome = future.result()          # process() never raises
            label = "downloaded" if outcome.downloaded else outcome.note.split(":", 1)[0]
            counts[label] += 1
            total_bytes += outcome.size
            report.writerow([
                outcome.row.number, label, outcome.note, outcome.size,
                outcome.row.url, outcome.row.title,
            ])
            fh.flush()
            LOGGER.info(
                "[%d/%d] row %d %-10s %s", done, len(todo), outcome.row.number,
                label, outcome.note if not outcome.downloaded else outcome.row.title[:60],
            )
            pending.append(outcome)
            if len(pending) >= WRITE_CHUNK:
                write(pending)
                pending = []
        write(pending)

    LOGGER.info(
        "Done: downloaded %d (%.1f MB), failed %d, retry later %d | report %s",
        counts["downloaded"], total_bytes / 2**20, counts["failed"], counts["retry"],
        report_path,
    )
    if counts["downloaded"] and not args.dry_run:
        start_kb_sync()
    return 0


if __name__ == "__main__":
    sys.exit(main())
