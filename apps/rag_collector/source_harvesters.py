#!/usr/bin/env python3
"""
source_harvesters.py
====================

Second discovery channel for the collector: ask repositories for their holdings
directly instead of asking a search engine to find them.

Why this exists
---------------
Dorking saturated. Measured over 24h on the live box: 1,785 queries produced 26
new rows, because a web search returns the same capped page of results however
the query is reworded. Repository APIs have no such ceiling - they paginate
through the whole result set, deterministically, and they hand over the authors
with the metadata, so harvested rows arrive with column F already filled.

Two families of harvester
-------------------------
`KeywordHarvester`  - searched once per concept, one page per step, resumable by
                      (concept index, page). OpenAlex, CORE, archive.org,
                      Zenodo, DOAJ, HAL, Semantic Scholar, Crossref.
`OaiHarvester`      - OAI-PMH repositories, which cannot be searched by keyword:
                      it walks the whole repository with a resumption token and
                      keeps only records matching one of our concepts. AJOL,
                      World Bank, UCT, Stellenbosch, UWC, OAPEN, DOAB,
                      OpenEdition, Persee.

Every harvester returns `[]` rather than raising, so one dead endpoint can never
stop a long run. Sources that fail repeatedly are rested by `HarvestPlanner`.

Not wired to the sheet: a harvester only proposes `HarvestRecord`s. The caller
still reserves the URL, verifies it over HTTP and applies the relevance gate,
exactly as it does for search hits.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import requests

LOGGER = logging.getLogger("dork_collector.harvest")

API_TIMEOUT = (10, 45)
PAGE_SIZE = 50                  # records requested per step
MAX_PAGES_PER_CONCEPT = 20      # 1,000 records per concept per source is plenty
HOST_MIN_INTERVAL = 3.0         # seconds between calls to the same API host
SOURCE_REST_AFTER_FAILURES = 3  # consecutive failures before a source is rested
SOURCE_REST_SECONDS = 1800.0

USER_AGENT = "SoakinGarri-rag-collector/1.0 (+https://soakingarri.com)"

#: Keep only records whose own metadata contains the concept phrase.
#:
#: Every source matches more loosely than it appears to. Crossref answered
#: "Nok culture" with Danish articles containing the word "nok" ("enough");
#: archive.org's quoted search matches a book's full text, so "Rome and the
#: ancient world" came back because Nok is mentioned inside it; OAI repositories
#: are matched locally and the World Bank's catalogue offered Vietnam poverty
#: reports. Column A labels every row with its concept, so a loose match is a
#: mislabelled row. Recall is not the constraint here - there are millions of
#: candidate records - so precision wins. Set HARVEST_STRICT=0 to disable.
STRICT_PHRASE_MATCH = os.getenv("HARVEST_STRICT", "1") != "0"

#: Credits that mark a text as machine-written, not a source.
#:
#: archive.org carries whole series of LLM-written "case studies" uploaded with
#: the model named as a co-author - 711 items from one uploader alone, with
#: titles that look exactly like the scholarship we want. They are generated
#: prose about history, not evidence of it, so they are dropped outright.
#: Matched on the vendor, never on "Claude" alone: Claude is a given name
#: (Claude Levi-Strauss), and dropping it would lose real authors.
AI_AUTHOR_MARKERS = (
    "anthropic", "openai", "chatgpt", "gpt-4", "gpt-3", "deepseek",
    "google gemini", "gemini (google", "copilot", "midjourney", "llama 3",
)


def looks_machine_written(authors: Iterable[str]) -> bool:
    joined = " ".join(str(a or "") for a in authors).lower()
    return any(marker in joined for marker in AI_AUTHOR_MARKERS)


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, pad with spaces."""
    return " " + re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip() + " "


def phrase_in_text(concept: str, text: str) -> bool:
    """True if the whole concept phrase appears in `text`, ignoring punctuation."""
    needle = _normalise(concept).strip()
    return bool(needle) and needle in _normalise(text)


@dataclass
class HarvestRecord:
    """One candidate document, before verification."""

    url: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    concept: str = ""
    source: str = ""
    #: True when the source matched loosely (keyword relevance rather than the
    #: exact phrase), so the caller should re-check the topic before storing it.
    needs_relevance_check: bool = False


def _http_url(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.lower().startswith(("http://", "https://")) else ""


class _Session:
    """Shared session with a per-host minimum interval, so we stay welcome."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._last_call: dict[str, float] = {}

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        self._wait(url)
        kwargs.setdefault("timeout", API_TIMEOUT)
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        self._wait(url)
        kwargs.setdefault("timeout", API_TIMEOUT)
        return self.session.post(url, **kwargs)

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_call.get(host, 0.0)
        gap = HOST_MIN_INTERVAL - (time.monotonic() - last)
        if gap > 0:
            time.sleep(gap)
        self._last_call[host] = time.monotonic()

    def json(self, url: str, *, method: str = "GET", **kwargs: Any) -> Any:
        response = self.get(url, **kwargs) if method == "GET" else self.post(url, **kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        return response.json()


# --------------------------------------------------------------------------- #
#  Keyword harvesters
# --------------------------------------------------------------------------- #

class KeywordHarvester:
    """Base class: one page of results for one concept."""

    name = "keyword"
    #: Paging is by page number; sources counting in records convert internally.
    page_size = PAGE_SIZE
    #: Set on sources that rank by relevance instead of matching the phrase:
    #: their tail results drift off-topic, so rows need re-checking.
    fuzzy = False

    def __init__(self, http: _Session) -> None:
        self.http = http

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:  # pragma: no cover
        raise NotImplementedError

    # Helpers shared by subclasses ------------------------------------------ #

    def _record(self, url: Any, title: Any, authors: Iterable[Any], concept: str) -> HarvestRecord | None:
        url = _http_url(url)
        if not url:
            return None
        names = [str(a).strip() for a in authors if str(a or "").strip()]
        return HarvestRecord(
            url=url, title=re.sub(r"\s+", " ", str(title or "")).strip(),
            authors=names, concept=concept, source=self.name,
            needs_relevance_check=self.fuzzy,
        )

    @staticmethod
    def _phrase(concept: str) -> str:
        return f'"{concept}"'


class OpenAlexHarvester(KeywordHarvester):
    """
    OpenAlex: open-access works, with the PDF link and full author list.

    The richest single source we have - it indexes repositories we would never
    think to query individually. Its search backend 503s occasionally; that is a
    rest, not a reason to drop it.
    """

    name = "openalex"
    fuzzy = True

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json("https://api.openalex.org/works", params={
            "filter": f"title_and_abstract.search:{concept},open_access.is_oa:true",
            "per-page": self.page_size,
            "page": page,
            "select": "display_name,best_oa_location,authorships",
        })
        out = []
        for work in data.get("results") or []:
            location = work.get("best_oa_location") or {}
            authors = [
                (a.get("author") or {}).get("display_name", "")
                for a in work.get("authorships") or []
            ]
            record = self._record(
                location.get("pdf_url") or location.get("landing_page_url"),
                work.get("display_name"), authors, concept,
            )
            if record:
                out.append(record)
        return out


class CoreHarvester(KeywordHarvester):
    """
    CORE v3 aggregates ~3,000 repositories.

    Needs a (free) API key: keyless responses come back without `downloadUrl`,
    so there is nothing to collect. Set CORE_API_KEY to switch it on.
    """

    name = "core"
    # Keyless CORE throttles hard: 10 per request is what it reliably serves,
    # and a wider `q` matches every word anywhere, so search the title field.
    page_size = 10

    def __init__(self, http: _Session, api_key: str = "") -> None:
        super().__init__(http)
        self.api_key = api_key

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json(
            "https://api.core.ac.uk/v3/search/works", method="POST",
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            json={
                "q": f'title:{self._phrase(concept)}',
                "limit": self.page_size,
                "offset": (page - 1) * self.page_size,
            },
        )
        out = []
        for work in data.get("results") or []:
            urls = [work.get("downloadUrl")] + [
                link.get("url") for link in work.get("links") or []
                if str(link.get("type", "")).lower() in ("download", "reader")
            ]
            authors = [a.get("name") for a in work.get("authors") or []]
            for url in urls:
                record = self._record(url, work.get("title"), authors, concept)
                if record:
                    out.append(record)
                    break
        return out


class ArchiveOrgHarvester(KeywordHarvester):
    """archive.org texts. `creator` is curated, so authors come free."""

    name = "archive.org"

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json("https://archive.org/advancedsearch.php", params={
            "q": f"{self._phrase(concept)} AND mediatype:texts",
            "fl[]": ["identifier", "creator", "title"],
            "rows": self.page_size, "page": page, "output": "json",
        })
        out = []
        for doc in ((data.get("response") or {}).get("docs")) or []:
            identifier = str(doc.get("identifier") or "")
            if not identifier:
                continue
            creator = doc.get("creator") or []
            record = self._record(
                f"https://archive.org/details/{quote(identifier)}",
                doc.get("title"),
                creator if isinstance(creator, list) else [creator],
                concept,
            )
            if record:
                out.append(record)
        return out


class ZenodoHarvester(KeywordHarvester):
    name = "zenodo"
    page_size = 25  # the API rejects anything larger

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json("https://zenodo.org/api/records", params={
            "q": self._phrase(concept), "size": self.page_size, "page": page,
        })
        out = []
        for hit in ((data.get("hits") or {}).get("hits")) or []:
            meta = hit.get("metadata") or {}
            files = hit.get("files") or []
            pdf = next(
                (f for f in files if str(f.get("key", "")).lower().endswith(".pdf")),
                files[0] if files else None,
            )
            url = (pdf or {}).get("links", {}).get("self") or (hit.get("links") or {}).get("self_html")
            record = self._record(
                url, meta.get("title"),
                [c.get("name") for c in meta.get("creators") or []], concept,
            )
            if record:
                out.append(record)
        return out


class DoajHarvester(KeywordHarvester):
    """DOAJ: open-access journal articles, many African titles."""

    name = "doaj"

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json(
            f"https://doaj.org/api/search/articles/{quote(self._phrase(concept))}",
            params={"pageSize": self.page_size, "page": page},
        )
        out = []
        for item in data.get("results") or []:
            bib = item.get("bibjson") or {}
            links = bib.get("link") or []
            url = next(
                (l.get("url") for l in links if str(l.get("type", "")).lower() == "fulltext"),
                (links[0] or {}).get("url") if links else None,
            )
            record = self._record(
                url, bib.get("title"),
                [a.get("name") for a in bib.get("author") or []], concept,
            )
            if record:
                out.append(record)
        return out


class HalHarvester(KeywordHarvester):
    """HAL: French-language scholarship, strong on francophone Africa."""

    name = "hal"

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json("https://api.archives-ouvertes.fr/search/", params={
            "q": self._phrase(concept),
            "fl": "title_s,fileMain_s,uri_s,authFullName_s",
            "rows": self.page_size, "start": (page - 1) * self.page_size, "wt": "json",
        })
        out = []
        for doc in ((data.get("response") or {}).get("docs")) or []:
            title = doc.get("title_s")
            record = self._record(
                doc.get("fileMain_s") or doc.get("uri_s"),
                title[0] if isinstance(title, list) else title,
                doc.get("authFullName_s") or [], concept,
            )
            if record:
                out.append(record)
        return out


class SemanticScholarHarvester(KeywordHarvester):
    """
    Semantic Scholar, open-access PDFs only.

    Unauthenticated use is aggressively rate limited; a 429 rests the source.
    Set SEMANTIC_SCHOLAR_API_KEY to lift the limits.
    """

    name = "semanticscholar"
    page_size = 25
    fuzzy = True

    def __init__(self, http: _Session, api_key: str = "") -> None:
        super().__init__(http)
        self.api_key = api_key

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        headers = {"x-api-key": self.api_key} if self.api_key else None
        data = self.http.json(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": concept, "limit": self.page_size,
                "offset": (page - 1) * self.page_size,
                "fields": "title,openAccessPdf,authors",
            },
            headers=headers,
        )
        out = []
        for paper in data.get("data") or []:
            pdf = (paper.get("openAccessPdf") or {}).get("url")
            record = self._record(
                pdf, paper.get("title"),
                [a.get("name") for a in paper.get("authors") or []], concept,
            )
            if record:
                out.append(record)
        return out


class CrossrefHarvester(KeywordHarvester):
    """
    Crossref, restricted to records advertising a full-text link.

    Lowest-yield source here because many of those links are paywalled - the
    usual verification and open-access checks sort that out downstream.
    """

    name = "crossref"
    fuzzy = True

    def fetch(self, concept: str, page: int) -> list[HarvestRecord]:
        data = self.http.json("https://api.crossref.org/works", params={
            "query.bibliographic": concept,
            "filter": "has-full-text:true,type:journal-article",
            "rows": self.page_size, "offset": (page - 1) * self.page_size,
            "select": "title,author,link",
        })
        out = []
        for item in ((data.get("message") or {}).get("items")) or []:
            pdf = next(
                (l.get("URL") for l in item.get("link") or []
                 if "pdf" in str(l.get("content-type", "")).lower()),
                None,
            )
            title = item.get("title") or []
            authors = [
                " ".join(filter(None, (a.get("given"), a.get("family"))))
                for a in item.get("author") or []
            ]
            record = self._record(pdf, title[0] if title else "", authors, concept)
            if record:
                out.append(record)
        return out


# --------------------------------------------------------------------------- #
#  OAI-PMH harvesters
# --------------------------------------------------------------------------- #

#: Repositories that answer OAI-PMH, verified reachable from the collector box.
OAI_REPOSITORIES: tuple[tuple[str, str], ...] = (
    ("ajol", "https://www.ajol.info/index.php/index/oai"),
    ("worldbank-okr", "https://openknowledge.worldbank.org/server/oai/request"),
    ("uct", "https://open.uct.ac.za/oai/request"),
    ("stellenbosch", "https://scholar.sun.ac.za/server/oai/request"),
    ("uwc", "https://etd.uwc.ac.za/server/oai/request"),
    ("oapen", "https://library.oapen.org/oai/request"),
    ("doab", "https://directory.doabooks.org/oai/request"),
    ("openedition", "https://oai.openedition.org/"),
    ("persee", "http://oai.persee.fr/oai"),
)

_DC = "{http://purl.org/dc/elements/1.1/}"
_OAI = "{http://www.openarchives.org/OAI/2.0/}"


class OaiHarvester:
    """
    Walks one OAI-PMH repository with a resumption token.

    OAI-PMH has no keyword search: a request returns the next slice of the whole
    repository. So each record is matched against our concept list and dropped
    unless one matches - which is also how it gets its column A label.
    """

    def __init__(self, repo: str, base_url: str, http: _Session,
                 match_concept: Callable[[str], str]) -> None:
        self.name = f"oai:{repo}"
        self.base_url = base_url
        self.http = http
        self.match_concept = match_concept

    def fetch_page(self, token: str | None) -> tuple[list[HarvestRecord], str | None]:
        """Returns (records, next token). A token of None means start over."""
        params = (
            {"verb": "ListRecords", "resumptionToken": token}
            if token else
            {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
        )
        response = self.http.get(self.base_url, params=params,
                                 headers={"Accept": "application/xml"})
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        root = ElementTree.fromstring(response.content)

        error = root.find(f"{_OAI}error")
        if error is not None:
            # An expired/invalid token is recoverable: restart this repository.
            code = error.get("code", "")
            LOGGER.info("%s: OAI error %s; restarting from the top", self.name, code)
            if token:
                return [], None
            raise RuntimeError(f"OAI error {code}")

        records: list[HarvestRecord] = []
        for node in root.iter(f"{_OAI}record"):
            record = self._parse(node)
            if record:
                records.append(record)

        token_node = root.find(f"{_OAI}ListRecords/{_OAI}resumptionToken")
        next_token = (token_node.text or "").strip() if token_node is not None else ""
        return records, next_token or None

    def _parse(self, node: ElementTree.Element) -> HarvestRecord | None:
        header = node.find(f"{_OAI}header")
        if header is not None and header.get("status") == "deleted":
            return None
        meta = node.find(f"{_OAI}metadata")
        if meta is None:
            return None

        def values(tag: str) -> list[str]:
            return [(e.text or "").strip() for e in meta.iter(f"{_DC}{tag}") if (e.text or "").strip()]

        titles = values("title")
        if not titles:
            return None
        title = re.sub(r"\s+", " ", titles[0])

        # Match on everything textual, then label the row with that concept.
        haystack = " ".join(titles + values("subject") + values("description")[:1])
        concept = self.match_concept(haystack)
        if not concept:
            return None

        url = next((u for u in (_http_url(v) for v in values("identifier")) if u), "")
        if not url:
            return None
        return HarvestRecord(url=url, title=title, authors=values("creator"),
                             concept=concept, source=self.name)


# --------------------------------------------------------------------------- #
#  Planner
# --------------------------------------------------------------------------- #

@dataclass
class _SourceState:
    concept_index: int = 0
    page: int = 1
    token: str | None = None
    exhausted: bool = False
    failures: int = 0
    resting_until: float = 0.0


class HarvestPlanner:
    """
    Rotates over every source, one page per `step()`, and remembers where it got
    to so a restart resumes instead of re-harvesting.

    State is a plain dict (persisted by the caller alongside `state.json`):
        {"sources": {"<name>": {"concept_index": 3, "page": 2, ...}}}
    """

    def __init__(
        self,
        concepts: Sequence[str],
        match_concept: Callable[[str], str],
        state: dict[str, Any] | None = None,
        semantic_scholar_key: str = "",
        core_key: str = "",
        enable_oai: bool = True,
    ) -> None:
        self.concepts = list(concepts)
        self.http = _Session()
        self.keyword: list[KeywordHarvester] = [
            OpenAlexHarvester(self.http),
            ArchiveOrgHarvester(self.http),
            ZenodoHarvester(self.http),
            DoajHarvester(self.http),
            HalHarvester(self.http),
            SemanticScholarHarvester(self.http, semantic_scholar_key),
            CrossrefHarvester(self.http),
        ]
        if core_key:  # keyless CORE returns no download links at all
            self.keyword.append(CoreHarvester(self.http, core_key))
        self.oai: list[OaiHarvester] = (
            [OaiHarvester(repo, url, self.http, match_concept) for repo, url in OAI_REPOSITORIES]
            if enable_oai else []
        )
        self._names = [h.name for h in self.keyword] + [h.name for h in self.oai]
        self._cursor = 0
        self.states: dict[str, _SourceState] = {}
        self._load(state or {})

    # -- state --------------------------------------------------------------- #

    def _load(self, state: dict[str, Any]) -> None:
        stored = state.get("sources") or {}
        for name in self._names:
            raw = stored.get(name) or {}
            self.states[name] = _SourceState(
                concept_index=int(raw.get("concept_index", 0)),
                page=int(raw.get("page", 1)),
                token=raw.get("token") or None,
                exhausted=bool(raw.get("exhausted", False)),
            )
        self._cursor = int(state.get("cursor", 0))

    def dump(self) -> dict[str, Any]:
        return {
            "cursor": self._cursor,
            "sources": {
                name: {
                    "concept_index": s.concept_index,
                    "page": s.page,
                    "token": s.token,
                    "exhausted": s.exhausted,
                }
                for name, s in self.states.items()
            },
        }

    # -- stepping ------------------------------------------------------------ #

    def step(self) -> list[HarvestRecord]:
        """
        Harvest one page from the next source due. Returns [] when every source
        is resting or finished - never raises.
        """
        for _ in range(len(self._names)):
            harvester = self._next_harvester()
            if harvester is None:
                continue
            state = self.states[harvester.name]
            try:
                records = (
                    self._step_oai(harvester, state)
                    if isinstance(harvester, OaiHarvester)
                    else self._step_keyword(harvester, state)
                )
            except Exception as exc:
                state.failures += 1
                if state.failures >= SOURCE_REST_AFTER_FAILURES:
                    state.resting_until = time.monotonic() + SOURCE_REST_SECONDS
                    state.failures = 0
                    LOGGER.warning("%s: resting for %.0f min after repeated failures (%s)",
                                   harvester.name, SOURCE_REST_SECONDS / 60, exc)
                else:
                    LOGGER.info("%s: %s", harvester.name, exc)
                continue

            state.failures = 0
            records = self._filter(harvester, records)
            if records:
                LOGGER.info("%s -> %d candidate(s)", harvester.name, len(records))
                return records
        return []

    @staticmethod
    def _filter(harvester: "KeywordHarvester | OaiHarvester",
                records: list[HarvestRecord]) -> list[HarvestRecord]:
        """Drop records whose title does not carry the concept phrase."""
        if isinstance(harvester, OaiHarvester):
            records = [r for r in records if not looks_machine_written(r.authors)]
        if not STRICT_PHRASE_MATCH or isinstance(harvester, OaiHarvester):
            return records  # OAI records were phrase-matched when parsed
        kept = [
            r for r in records
            if phrase_in_text(r.concept, r.title) and not looks_machine_written(r.authors)
        ]
        dropped = len(records) - len(kept)
        if dropped:
            LOGGER.debug("%s: dropped %d record(s) (no concept phrase, or AI-written)",
                         harvester.name, dropped)
        return kept

    def _next_harvester(self) -> KeywordHarvester | OaiHarvester | None:
        """Round-robin, skipping sources that are resting or finished."""
        pool: list[KeywordHarvester | OaiHarvester] = [*self.keyword, *self.oai]
        if not pool:
            return None
        for _ in range(len(pool)):
            harvester = pool[self._cursor % len(pool)]
            self._cursor = (self._cursor + 1) % len(pool)
            state = self.states[harvester.name]
            if state.exhausted or time.monotonic() < state.resting_until:
                continue
            return harvester
        return None

    def _step_keyword(self, harvester: KeywordHarvester, state: _SourceState) -> list[HarvestRecord]:
        if not self.concepts:
            state.exhausted = True
            return []
        if state.concept_index >= len(self.concepts):
            # A full pass is done; go round again - repositories gain new items.
            state.concept_index = 0
            state.page = 1
            LOGGER.info("%s: completed a full pass over %d concepts", harvester.name, len(self.concepts))
        concept = self.concepts[state.concept_index]
        records = harvester.fetch(concept, state.page)

        if len(records) < harvester.page_size or state.page >= MAX_PAGES_PER_CONCEPT:
            state.concept_index += 1     # this concept is done on this source
            state.page = 1
        else:
            state.page += 1
        return records

    def _step_oai(self, harvester: OaiHarvester, state: _SourceState) -> list[HarvestRecord]:
        records, next_token = harvester.fetch_page(state.token)
        state.token = next_token
        if next_token is None:
            # Reached the end of the repository; a fresh walk starts next time.
            LOGGER.info("%s: reached the end of the repository", harvester.name)
        return records

    # -- reporting ----------------------------------------------------------- #

    def summary(self) -> str:
        parts = []
        for name, s in self.states.items():
            if s.exhausted:
                parts.append(f"{name}=done")
            elif name.startswith("oai:"):
                parts.append(f"{name}={'walking' if s.token else 'start'}")
            else:
                parts.append(f"{name}={s.concept_index}/{len(self.concepts)}")
        return " ".join(parts)


def concept_matcher(concepts: Sequence[str]) -> Callable[[str], str]:
    """
    Build the matcher `OaiHarvester` needs: text -> matching concept, or "".

    OAI-PMH cannot be searched, so a whole repository streams past and each
    record is matched here against the concept list. The match is a phrase
    match on the record's title, subjects and description: a token-overlap
    score let single-distinctive-word concepts ("African trade" -> "trade")
    match anything, which is how a walk of the World Bank's catalogue started
    proposing Vietnam poverty reports.

    The longest matching concept wins, so "Kingdom of Kush" beats "Kush".
    """
    ordered = sorted({c for c in concepts if c.strip()}, key=len, reverse=True)

    def match(text: str) -> str:
        haystack = _normalise(text)
        for concept in ordered:
            if _normalise(concept).strip() in haystack:
                return concept
        return ""

    return match
