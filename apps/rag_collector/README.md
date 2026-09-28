# RAG Dork Collector

Long-running discovery service that finds open-access academic / historical /
educational documents, verifies each URL, attributes it, and appends the cleaned
metadata into a Google Sheet. Runs unattended on an EC2 box.

It discovers through two channels: **dorking** search engines, and **harvesting**
repository APIs directly (`source_harvesters.py`). Dorking saturated at ~26 new
documents per 1,785 queries, because a web search caps its result set however the
query is reworded; harvesting paginates through whole catalogues instead, and
brings the authors with it.

## Files

| File | Purpose |
| --- | --- |
| `dork_sheet_collector.py` | The collector (SheetsManager, SearchScraper, URLProcessor, Collector). |
| `author_resolver.py` | Finds a document's author(s) for column F. |
| `source_harvesters.py` | Repository harvesters (OpenAlex, archive.org, Zenodo, DOAJ, HAL, OAI-PMH, …). |
| `backfill_authors.py` | One-off: fills blank column F cells on existing rows. |
| `requirements.txt` | Pinned dependencies. |
| `setup_ec2.sh` | Ubuntu provisioning + `soakingarri-scraper.service` systemd unit. |
| `service_account.json` | Google credential (gitignored, local only). Found beside the script. |
| `logs/` | Runtime logs and backfill CSVs (gitignored). |

## Sheet contract

Target: `1SkQTmXcmoLAAzx2ePvdaiprbN9X_CQoNkFTRU3VFIOQ`, tab `Sheet1` (falls back
to the first tab if that name is missing).

| Col | Field | Notes |
| --- | --- | --- |
| A | Subject / Concept | The dork concept that surfaced the hit. |
| B | Source | Host, e.g. `unesdoc.unesco.org`. |
| C | Hyperlink | **Dedup key** — normalised before comparison. |
| D | Title of source | `citation_title` / `og:title` / `<title>` / search title / filename. |
| E | Open source | `TRUE` / `FALSE`. |
| F | Author | `A; B; C` (7+ names collapse to `...; et al.`). Blank = unknown. |

## One-time Google setup

1. Enable the **Google Sheets API** (and Drive API) on the service account's project.
2. Open the Sheet → **Share** → paste the service account's `client_email` → **Editor**.
   Without this the script fails at startup with a 403 — it is the single most
   common cause of "it won't start".

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python dork_sheet_collector.py --dry-run --once --results 5   # smoke test
python dork_sheet_collector.py                                # run forever
```

Useful flags: `--sheet-id`, `--worksheet`, `--batch-size`, `--results`,
`--workers`, `--once`, `--max-queries`, `--dry-run`, `--log-level DEBUG`.

> `--once` runs a **full** cycle — ~160 queries, roughly half an hour. For a
> quick check always pair it with `--max-queries N`.
Env equivalents: `SHEET_ID`, `WORKSHEET_NAME`, `LOG_LEVEL`,
`GOOGLE_APPLICATION_CREDENTIALS`.

## Deploy to EC2

Copy the app files **individually** — never `scp -r` a directory that might
contain your `.pem`, or you upload your own SSH private key to the box.

```bash
# Run from apps/rag_collector. The key lives in the repo's gitignored .local/.
# Amazon Linux 2023 uses ec2-user; Ubuntu images use ubuntu.
KEY=../../.local/keys/sg-key.pem
HOST=ec2-user@<EC2_IP>
ssh -i $KEY $HOST 'mkdir -p ~/rag_collector'
scp -i $KEY dork_sheet_collector.py author_resolver.py backfill_authors.py \
    source_harvesters.py requirements.txt setup_ec2.sh README.md $HOST:~/rag_collector/
scp -i $KEY service_account.json $HOST:~/rag_collector/
ssh -i $KEY $HOST 'cd ~/rag_collector && chmod +x setup_ec2.sh && ./setup_ec2.sh'
```

The box's `~/rag_collector` is a flat copy of this directory (not a git
checkout), so keep the modules flat here too: they import each other by name
and `setup_ec2.sh` resolves everything relative to its own folder.

`setup_ec2.sh` detects `dnf`/`yum`/`apt`, installs Python 3.11 where the system
Python is older (Amazon Linux 2023 ships 3.9), builds the venv, runs a short
`--max-queries 2` dry-run smoke test, then enables and starts the service.
Follow it with `sudo journalctl -u soakingarri-scraper -f`.

Currently deployed to `ec2-user@3.80.73.164` (Amazon Linux 2023, t3.micro).

## How it behaves

- **Dedup** — column C is read into a `set()` at startup; URLs are normalised
  (host lowercased, `www.`/tracking params/fragment stripped) and *reserved*
  before verification, so concurrent workers can't double-queue one document.
- **Batching** — rows buffer to 8 (`--batch-size`) before a single
  `append_rows` call. On failure the batch is pushed back onto the buffer, not
  dropped.
- **Backoff** — search retries 5× with doubling delay + jitter; three
  consecutive query failures triggers a 5–30 min cool-down. Sheets retries
  429/5xx with capped exponential backoff and fails fast on 401/403/404.
- **Pacing** — 5–12 s between searches, 60–120 s between categories, 5–10 min
  between full cycles.
- **Shutdown** — SIGTERM/SIGINT flush the buffer before exit, so
  `systemctl restart` never loses rows.
- **State** — `state.json` keeps cycle/query counters across restarts; the cycle
  number seeds query shuffling and modifier rotation so each pass differs.

## Authors (column F)

`author_resolver.py` tries these in order and stops at the first answer:

1. **Repository APIs** - archive.org item `creator`, ERIC (including archive.org's
   `ERIC_*` mirrors, whose creator is just "ERIC").
2. **Landing-page tags** - `citation_author`, `DC.creator`, JSON-LD.
3. **DOI** in the URL or printed on the PDF's first pages, looked up on Crossref.
4. **PDF author metadata**, only if that full name is printed in the document.
5. **Title search** (Crossref, then OpenAlex): near-exact title match, and the
   author's full name must appear in the document text.
6. **LLM** (gpt-4o-mini, only when `OPENAI_API_KEY` is set) reads the PDF's first
   pages or an archive.org item's OCR text and returns the byline. Names not
   printed in that text are discarded.

Measured on 200 random rows of the live sheet: authors found for **68%**. Most
of the remainder genuinely have no author (TV captures, municipal reports,
course pages). Every heuristic step is corroborated against the document text,
because a wrong author is worse than a blank - see the module docstring for the
failures that shaped each check.

- Disable with `--no-authors`. Without `OPENAI_API_KEY`, steps 1-5 still run.
- `AUTHOR_PDF_MAX_MB` caps PDF downloads (default 20; the t3.micro uses 8).
- `CROSSREF_MAILTO` (optional) joins Crossref's faster "polite" pool.

Backfill rows collected before this existed (safe while the service runs; it
only writes blank F cells and re-checks each row's URL before writing):

```bash
.venv/bin/python backfill_authors.py --dry-run --limit 20   # preview
.venv/bin/python backfill_authors.py                        # fill all blanks
```

On the EC2 box the key lives in `~/rag_collector/collector.env` (mode 600),
loaded by the systemd drop-in `soakingarri-scraper.service.d/authors.conf`.

## Sources (harvesting)

`source_harvesters.py` asks repositories for their holdings instead of asking a
search engine to find them. One page from one source per step, rotating, with a
resume cursor per source in `state.json` so a restart continues where it left off.

**Keyword sources** — searched once per concept, paging until the concept is
exhausted (cap `MAX_PAGES_PER_CONCEPT`):

| Source | Notes |
| --- | --- |
| OpenAlex | Richest single source: OA works with PDF link + full author list. 503s periodically. |
| archive.org | `advancedsearch`; curated `creator` field. |
| Zenodo | Page size capped at 25 by the API. |
| DOAJ | Open-access journals, many African titles. |
| HAL | Francophone scholarship. |
| Semantic Scholar | OA PDFs; heavily throttled without `SEMANTIC_SCHOLAR_API_KEY`. |
| Crossref | Records advertising a full-text link; lowest yield, most drift. |
| CORE | **Needs `CORE_API_KEY`** — keyless responses omit the download links, so it stays off. |

**OAI-PMH repositories** — cannot be searched, so the walk streams the whole
repository and keeps only records matching a concept: AJOL, World Bank OKR, UCT,
Stellenbosch, UWC, OAPEN, DOAB, OpenEdition, Persée.

### Precision, and why it is strict

Every source matches more loosely than it looks. Crossref answered "Nok culture"
with Danish articles containing *nok* ("enough"); archive.org's quoted search
matches a book's whole text, so "Rome and the ancient world" came back for Nok;
a token-overlap match on OAI records turned "African trade" into "trade" and the
World Bank walk started offering Vietnam poverty reports. Column A labels every
row with its concept, so a loose match is a **mislabelled** row.

So a record is kept only when its own metadata contains the whole concept phrase
(`HARVEST_STRICT=0` disables this). Records crediting an AI model as author are
dropped outright — archive.org carries 700+ LLM-written "case studies" from one
uploader whose titles look just like real scholarship. The vendor name is what
matches, never "Claude" alone, which is a given name.

Harvested URLs skip `looks_promising` (a repository catalogue already knows what
a document is) and may be landing pages; `citation_pdf_url` is followed to the
file where the page advertises one.

```bash
python dork_sheet_collector.py --harvest-only   # repositories only, no searching
python dork_sheet_collector.py --no-harvest     # searching only (old behaviour)
python dork_sheet_collector.py --no-oai         # skip the whole-repository walks
```

## Search backend (important)

Install **`ddgs`**, not `duckduckgo-search`. The latter was renamed and is now
sunset — every query against it returns zero results. The collector imports
whichever is present, but only `ddgs` works.

Backends are rotated per query and per retry in this order:

```python
SEARCH_BACKENDS = ("brave", "bing", "auto", "duckduckgo")
```

This order is empirical. `brave` and `bing` honour `filetype:` and `site:`
faithfully (8/8 PDFs on a test dork); the plain `duckduckgo` backend ignores the
operators *and* frequently returns nothing. An empty result set is treated as
"this backend is unhappy" and rotates to the next one after a ~3 s pause rather
than triggering the full rate-limit backoff.

Because operator support is still imperfect, `looks_promising()` pre-filters
results before any HTTP probe. A candidate must be either:

- **PDF-shaped** — `.pdf`, `/pdf`, or `getpdf` in the URL; or
- **a repository *item* page** — on an open-access host *and* matching
  `REPOSITORY_ITEM_PATTERNS` (`/details/`, `/ark:/`, `/handle/123`,
  `/records/123`, `/download/pdf`, …).

That second condition is deliberately narrow. "Any HTML on an open-access host"
is far too loose: it admits `openstax.org/books/psychology-2e/pages/preface` and
even `help.openstax.org/.../faculty` (a login page), because `help.openstax.org`
matches `openstax.org` by suffix. Requiring a named item is what keeps prefaces,
browse pages and login forms out of the dataset.

Observed funnel on two live dorks: 20 raw results → 11 promising → **7 rows**.
Everything dropped was correctly dropped (Scribd, YouTube, Britannica, course
pages).

## Corpus size and exhaustion

The dork corpus is sized against the run's time budget, not picked arbitrarily.

| | |
|---|---|
| Categories | 6 |
| Concepts | 470 |
| Base queries per cycle (concept x dork) | **9,470** |
| Modifier variants | 11 |
| **Distinct query strings** | **104,170** |
| Harvest sources | 7 keyword APIs + 9 OAI-PMH repositories |

The corpus is sized past the run's budget on purpose, so a long run keeps
finding new ground instead of re-asking answered questions. One search cycle now
takes well over a day, so `CYCLE_SLEEP_RANGE` is almost never reached.

Search yield decays as a topic is exhausted (measured: 26 new documents per
1,785 queries after 23 cycles); harvest yield does not decay the same way,
because each source paginates through its own catalogue. Adding concepts widens
both channels at once - the harvesters query per concept too.

If you extend the run beyond 3 days, grow `CATEGORIES` rather than lowering the
sleeps — pacing is what keeps the search backends answering.

## Tuning

Everything worth changing sits in the constants block at the top of
`dork_sheet_collector.py`: `SEARCH_SLEEP_RANGE`, `CYCLE_SLEEP_RANGE`,
`RESULTS_PER_QUERY`, `OPEN_ACCESS_DOMAINS`, `CLOSED_ACCESS_DOMAINS`, and the
`CATEGORIES` tuple (add concepts/dorks there).

If DuckDuckGo starts returning empty result sets for hours, you are rate
limited: raise `SEARCH_SLEEP_RANGE` and `CYCLE_SLEEP_RANGE` rather than
restarting the service in a loop.

## Security

`service_account.json` is a live credential. Keep it out of git (see
`.gitignore`), `chmod 600` it on the instance, and rotate it if it has ever been
committed or shared.
