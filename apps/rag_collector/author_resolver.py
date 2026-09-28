"""
author_resolver.py
==================

Works out who wrote a document the collector has found.

No single source knows the authors of every URL we collect, so the resolver
walks a ladder of strategies from most to least authoritative and stops at the
first one that produces a name:

    1. Repository APIs     archive.org item metadata, ERIC        (by URL shape)
    2. Landing-page meta   citation_author / DC.creator / JSON-LD (HTML only)
    3. DOI                 in the URL or printed on the PDF's first pages
                           -> Crossref
    4. PDF metadata        XMP dc:creator / Info /Author, only when that name is
                           printed in the document itself
    5. Title lookup        Crossref, then OpenAlex; near-exact title match, and
                           the author's name must be printed in the document
                           (or the title must be long enough to be distinctive)
    6. LLM (optional)      reads the opening text - the PDF's first pages, or
                           an archive.org item's OCR text - and returns the
                           byline; every name must be printed in that text

Steps 4-6 are hedged because of failures seen on the real sheet: embedded PDF
metadata is mostly whoever pressed "Save" ("Kyle", "scc", "Microsoft Word -
003 Bondarenko FIN"); title searches return a *book review* of the right book,
or a different book with the same short title; a surname-only check credited
"Colin A. Hope" with a dictionary because "hope" is an English word; an
unconstrained LLM credits press releases to the official they quote. A wrong
author is worse than an empty cell, so every heuristic answer is corroborated
against the document's own text before it is trusted.

`resolve()` never raises; an empty result simply means "unknown".
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

try:  # pragma: no cover - environment dependent
    import pypdf

    _HAVE_PYPDF = True
except ImportError:  # pragma: no cover - environment dependent
    _HAVE_PYPDF = False

LOGGER = logging.getLogger("dork_collector.authors")
# pypdf reports every recoverable xref glitch at WARNING; they are noise here.
logging.getLogger("pypdf").setLevel(logging.ERROR)

# --------------------------------------------------------------------------- #
#  Tuning
# --------------------------------------------------------------------------- #

API_TIMEOUT = (10, 20)          # (connect, read) seconds for metadata APIs
PDF_TIMEOUT = (10, 40)
# The whole file is needed (pypdf reads the xref at the end). Up to VERIFY_WORKERS
# downloads run at once, so keep this low on small boxes: the t3.micro runs 8.
PDF_MAX_BYTES = int(float(os.getenv("AUTHOR_PDF_MAX_MB", "20")) * 1024 * 1024)
PDF_TEXT_PAGES = 2              # title pages carry the byline and the DOI
MAX_AUTHORS_SHOWN = 6           # longer lists collapse to "..., et al."
TITLE_MATCH_MIN = 0.90          # similarity needed to trust a title search
# A title search is only trusted when the match is corroborated: either the
# found author's full name is printed in the document we read, or the title is
# long enough to be distinctive on its own. Short titles ("The Kingdom of
# Kush", "General History of Africa") matched *different* books in testing.
TITLE_MIN_WORDS = 4
TITLE_MIN_WORDS_UNCORROBORATED = 7

OPENAI_MODEL = os.getenv("AUTHOR_LLM_MODEL", "gpt-4o-mini")

# Values repositories and word processors put in "author" fields that are not
# authors. Compared after lowercasing and stripping punctuation.
JUNK_AUTHORS = frozenset(
    {
        "", "anonymous", "unknown", "admin", "administrator", "user", "owner",
        "author", "eric", "internet archive", "various", "n a", "na", "none",
        "windows user", "microsoft office user", "default", "hp", "dell",
        "lenovo", "acer", "asus", "toshiba", "pc", "compaq", "staff",
    }
)

# Fragments that mark a string as software, not a person.
JUNK_FRAGMENTS = (
    "microsoft", "word", "adobe", "acrobat", "pdf", "latex", "tex ", "writer",
    "http", "www.", ".com", "@", "copyright", "scanner", "scan", "converter",
)

DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
#  Name helpers
# --------------------------------------------------------------------------- #

def _fold(text: str) -> str:
    """Lowercase and strip accents, so 'Török' matches 'Torok' in PDF text."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    # PDF extraction can detach tone marks from their letter, leaving a
    # space before them (Yoruba 'OKE' became 'O <marks>KE'); rejoin first.
    decomposed = re.sub(r"\s+(?=[\u0300-\u036f])", "", decomposed)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def clean_name(raw: Any) -> str:
    """
    Normalise one author string to 'Given Family'.

    Handles catalogue forms such as
    ``Smith, R. Bosworth (Reginald Bosworth), 1839-1908`` -> ``R. Bosworth Smith``.
    """
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    name = re.sub(r"\([^)]*\)", "", name)                 # parenthetical expansions
    name = re.sub(r",?\s*(?:ca\.\s*)?\d{3,4}\s*-\s*(?:\d{3,4})?\.?$", "", name)  # life dates
    name = re.sub(r",?\s*\b(?:ed|eds|editor|author|comp)\.?$", "", name, flags=re.I)
    name = name.strip(" ,;.")
    # Role and honorific prefixes: "Assistant Editor I. Hrbek", "Dr Issoufou"
    name = re.sub(
        r"^(?:(?:assistant|associate|general|volume|chief)\s+)?(?:editors?|eds?\.|"
        r"dr\.?|prof\.?|professor|mr\.?|mrs\.?|ms\.?)\s+",
        "", name, flags=re.I,
    ).strip()

    # "Family, Given" -> "Given Family" (only a single comma: "A, B, C" is a list)
    if name.count(",") == 1:
        family, given = (part.strip() for part in name.split(","))
        if family and given:
            name = f"{given} {family}"

    name = re.sub(r"\s+", " ", name).strip()
    # Catalogues sometimes shout: "KATE EZRA" -> "Kate Ezra"
    if name.isupper() and len(name) > 4:
        name = name.title()
    return re.sub(r"\bunesco\b", "UNESCO", name, flags=re.I)


def is_plausible_person(name: str) -> bool:
    """Reject empty, software, placeholder and single-token names."""
    folded = re.sub(r"[^a-z ]+", " ", _fold(name)).strip()
    folded = re.sub(r"\s+", " ", folded)
    if folded in JUNK_AUTHORS or len(folded) < 4:
        return False
    if any(fragment in _fold(name) for fragment in JUNK_FRAGMENTS):
        return False
    if re.search(r"\d", name) or "_" in name:
        return False
    return len(folded.split()) >= 2


def is_plausible_creator(name: str) -> bool:
    """
    Looser gate for curated catalogue fields (archive.org, ERIC).

    Those are typed by a person on purpose, so corporate authors like
    'UNESCO' or 'Institute of Education Sciences' are legitimate there.
    """
    folded = re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", _fold(name))).strip()
    if folded in JUNK_AUTHORS or len(folded) < 3:
        return False
    if re.search(r"[_@\d]|www\.|https?:|\.(?:com|org|info|net)\b", name, re.I):
        return False  # uploader handles: "romaindavid4_hotmail", "Genesis2023"
    if " " not in name.strip():
        # A lone word is an uploader handle ("elgamelyan") unless it reads as an
        # organisation acronym ("UNESCO", "ERIC" is caught above as junk).
        return name.isupper() and len(name) >= 3
    return True


def tidy_extracted_name(raw: str) -> str:
    """
    Repair names lifted from PDF text rather than a catalogue.

    Text extraction detaches Yoruba tone marks from their letters
    ("Olusegun Oke" came out as "Olu <mark>se <mark>gun O <mark>KE") and
    bylines often capitalise surnames
    ("Djardaye MALAM ISSOUFOU").
    """
    name = unicodedata.normalize("NFD", raw)
    name = re.sub(r"\s+(?=[\u0300-\u036f\u00b4\u02b9-\u02ff])", "", name)  # reattach marks
    name = re.sub(r"(?<=\s)[\u0300-\u036f\u00b4\u02b9-\u02ff]+", "", name)
    name = unicodedata.normalize("NFC", name)
    name = " ".join(
        word.title() if word.isupper() and len(word) > 2 else word
        for word in clean_name(name).split()
    )
    return name


def split_name_list(raw: str) -> list[str]:
    """
    Split a field holding several people into one string per person.

    ``;``, `` and `` and ``&`` always separate people. A comma separates people
    only when both sides are multi-word ("Editor M. El Fasi, Assistant Editor
    I. Hrbek"); a comma between single words is "Family, Given" and is left
    for `clean_name` to reorder.
    """
    parts = re.split(r"\s*(?:;|\band\b|&)\s*", raw)
    out: list[str] = []
    for part in parts:
        pieces = [p.strip() for p in part.split(",")]
        if len(pieces) > 1 and all(len(p.split()) >= 2 for p in pieces):
            out.extend(pieces)
        else:
            out.append(part)
    return [p for p in out if p.strip()]


def unique_names(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = _fold(name)
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def format_authors(names: list[str]) -> str:
    """Render for the sheet: 'A; B; C', collapsing long lists to 'et al.'."""
    if len(names) > MAX_AUTHORS_SHOWN:
        return "; ".join(names[:MAX_AUTHORS_SHOWN]) + "; et al."
    return "; ".join(names)


def surname_in_text(name: str, folded_text: str) -> bool:
    """True if the family name (last token of 3+ letters) occurs as a word."""
    tokens = [t for t in re.findall(r"[a-z'\-]+", _fold(name)) if len(t) >= 3]
    return bool(tokens) and re.search(
        rf"(?<![a-z]){re.escape(tokens[-1])}(?![a-z])", folded_text
    ) is not None


def name_in_text(name: str, folded_text: str) -> bool:
    """
    Stricter corroboration: the family name printed next to the given name
    or its initial ("Colin Hope", "C. A. Hope", "Hope, Colin").

    Surname alone is not enough for names found *outside* the text: "Hope",
    "Young" or "Church" occur as ordinary words in almost any book, which is
    how a title search once credited Colin A. Hope with a dictionary he did
    not write.
    """
    tokens = re.findall(r"[a-z'\-]+", _fold(name))
    if len(tokens) < 2 or len(tokens[-1]) < 2:
        return False
    family, given = re.escape(tokens[-1]), tokens[0]
    first = rf"(?:{re.escape(given)}|{re.escape(given[0])}\.?)"
    patterns = (
        rf"(?<![a-z]){first}(?![a-z]).{{0,30}}?(?<![a-z]){family}(?![a-z])",
        rf"(?<![a-z]){family}(?![a-z])\s*,\s*{first}(?![a-z])",
    )
    return any(re.search(p, folded_text) for p in patterns)


def title_similarity(a: str, b: str) -> float:
    norm = lambda s: " ".join(re.findall(r"[a-z0-9]+", _fold(s)))  # noqa: E731
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _crossref_name(author: dict[str, Any]) -> str:
    if author.get("given") or author.get("family"):
        return clean_name(f"{author.get('given', '')} {author.get('family', '')}")
    return clean_name(author.get("name", ""))


# --------------------------------------------------------------------------- #
#  Resolver
# --------------------------------------------------------------------------- #

@dataclass
class AuthorResult:
    authors: list[str] = field(default_factory=list)
    method: str = ""               # which rung of the ladder answered

    def __bool__(self) -> bool:
        return bool(self.authors)

    @property
    def text(self) -> str:
        return format_authors(self.authors)


class AuthorResolver:
    """
    Thread-safe (one shared `requests.Session`, no mutable per-call state), so
    the collector can call it from its verification workers.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        use_llm: bool | None = None,
        openai_api_key: str | None = None,
        crossref_mailto: str | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.use_llm = bool(self.openai_api_key) if use_llm is None else use_llm
        # Crossref routes requests carrying a contact address to its faster
        # "polite" pool. Opt-in via env so no address is sent by default.
        mailto = crossref_mailto or os.getenv("CROSSREF_MAILTO", "")
        agent = "SoakinGarri-rag-collector/1.0"
        self.api_headers = {
            "User-Agent": f"{agent} (mailto:{mailto})" if mailto else agent,
            "Accept": "application/json",
        }

    # -- public -------------------------------------------------------------- #

    def resolve(self, url: str, title: str = "", html: str = "") -> AuthorResult:
        try:
            return self._resolve(url, title, html)
        except Exception as exc:  # never let author lookup cost us a row
            LOGGER.debug("Author resolution crashed for %s: %s", url, exc)
            return AuthorResult()

    # -- ladder -------------------------------------------------------------- #

    def _resolve(self, url: str, title: str, html: str) -> AuthorResult:
        host = (urlparse(url).netloc or "").lower()
        # archive.org full-text pages are titled 'Full text of "X"'.
        title = re.sub(r'^\s*full text of\s*"?(.*?)"?\s*$', r"\1", title or "", flags=re.I)
        # Opening text of the document, when we get to read it; used to
        # corroborate title-search hits and as the LLM's input.
        doc_text = ""

        # 1. Repository APIs keyed off the URL itself.
        eric_id = self._eric_id(url)
        if eric_id:
            result = self._from_eric(eric_id)
            if result:
                return result
        if host == "archive.org" or host.endswith(".archive.org"):
            doc_text, result = self._from_archive(url)
            if result:
                return result

        # 2. Scholarly landing pages publish Highwire/Dublin Core tags.
        if html:
            result = self._from_html(html)
            if result:
                return result

        # 3. A DOI in the URL (publisher links usually carry one).
        doi = self._doi_in(unquote(url))
        if doi:
            result = self._from_crossref_doi(doi)
            if result:
                return result

        # 4. Open the PDF: a printed DOI beats everything, then metadata.
        if not html and not doc_text and self._maybe_pdf(url):
            doc_text, result = self._from_pdf(url)
            if result:
                return result

        # 5. Search by title, trusting only a near-exact, corroborated match.
        words = len(re.findall(r"\w+", title or ""))
        if words >= TITLE_MIN_WORDS:
            folded = _fold(doc_text)
            for lookup in (self._from_crossref_title, self._from_openalex_title):
                result = lookup(title)
                if not result:
                    continue
                if doc_text:
                    confirmed = [n for n in result.authors if name_in_text(n, folded)]
                    if confirmed:
                        return AuthorResult(confirmed, result.method)
                elif words >= TITLE_MIN_WORDS_UNCORROBORATED:
                    return result

        # 6. Ask an LLM to read the byline, then check its answer.
        if self.use_llm and doc_text:
            result = self._from_llm(doc_text, title)
            if result:
                return result

        return AuthorResult()

    # -- 1. repositories ----------------------------------------------------- #

    @staticmethod
    def _eric_id(url: str) -> str:
        parsed = urlparse(url)
        match = re.search(r"\b(E[DJ]\d{5,7})\b", parsed.path, re.IGNORECASE)
        host = parsed.netloc.lower()
        if "eric.ed.gov" in host:
            if match:
                return match.group(1).upper()
            ids = parse_qs(parsed.query).get("id", [])
            return ids[0].upper() if ids and re.fullmatch(r"E[DJ]\d+", ids[0], re.I) else ""
        # archive.org mirrors ERIC as "ERIC_EJ1077605" with creator "ERIC".
        match = re.search(r"ERIC_(E[DJ]\d{5,7})", url, re.IGNORECASE)
        return match.group(1).upper() if match else ""

    def _from_eric(self, eric_id: str) -> AuthorResult:
        data = self._get_json(
            "https://api.ies.ed.gov/eric/",
            params={"search": f"id:{eric_id}", "fields": "id,author", "format": "json"},
        )
        docs = ((data or {}).get("response") or {}).get("docs") or []
        names = [clean_name(a) for a in (docs[0].get("author") or [])] if docs else []
        names = unique_names(n for n in names if is_plausible_creator(n))
        return AuthorResult(names, "eric") if names else AuthorResult()

    @staticmethod
    def _archive_identifier(url: str) -> str:
        parsed = urlparse(url)
        path = unquote(parsed.path)
        if "view_archive.php" in path:
            path = parse_qs(parsed.query).get("archive", [""])[0]
        match = re.search(r"/(?:details|stream|download|embed|items)/([^/?#]+)", path)
        return match.group(1) if match else ""

    def _from_archive(self, url: str) -> tuple[str, AuthorResult]:
        """
        Returns ``(opening_text, result)``.

        About a third of archive.org items have no ``creator`` - typically a
        whole book uploaded by a member. For those we hand back the opening of
        the item's OCR text, so title search and the LLM can read the title
        page just as they would a PDF's.
        """
        identifier = self._archive_identifier(url)
        if not identifier:
            return "", AuthorResult()
        data = self._get_json(f"https://archive.org/metadata/{quote(identifier)}") or {}
        meta = data.get("metadata") or {}
        creators = meta.get("creator") or []
        if isinstance(creators, str):
            creators = [creators]
        creators = [part for c in creators for part in split_name_list(str(c))]
        names = unique_names(
            clean_name(c) for c in creators if is_plausible_creator(clean_name(c))
        )
        if names:
            return "", AuthorResult(names, "archive.org")
        return self._archive_text(identifier, url, data.get("files") or []), AuthorResult()

    def _archive_text(self, identifier: str, url: str, files: list[dict[str, Any]]) -> str:
        """First ~12 KB of the item's OCR text (``*_djvu.txt``), or ''."""
        texts = [f.get("name", "") for f in files if f.get("name", "").endswith("_djvu.txt")]
        if not texts:
            return ""
        # Multi-document items: prefer the text file the URL itself points at.
        wanted = unquote(urlparse(url).path).replace("+", " ")
        name = next((t for t in texts if t in wanted), texts[0])
        try:
            response = self.session.get(
                f"https://archive.org/download/{quote(identifier)}/{quote(name)}",
                headers={**self.api_headers, "Range": "bytes=0-12287"},
                timeout=API_TIMEOUT,
            )
            if response.status_code not in (200, 206):
                return ""
            return response.content[:12288].decode("utf-8", errors="replace")
        except requests.exceptions.RequestException as exc:
            LOGGER.debug("archive.org text fetch failed for %s: %s", identifier, exc)
            return ""

    # -- 2. HTML ------------------------------------------------------------- #

    @staticmethod
    def _meta_values(html: str, key: str) -> list[str]:
        """All `content` values of <meta name|property=key>, either attribute order."""
        key = re.escape(key)
        patterns = (
            rf'<meta[^>]+(?:name|property)=["\']{key}["\'][^>]*content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']{key}["\']',
        )
        values: list[str] = []
        for pattern in patterns:
            values += re.findall(pattern, html, re.IGNORECASE)
        return values

    def _from_html(self, html: str) -> AuthorResult:
        for key in ("citation_author", "DC.creator", "dc.creator", "author", "article:author"):
            names = unique_names(
                clean_name(v) for v in self._meta_values(html, key)
                if is_plausible_person(clean_name(v))
            )
            if names:
                return AuthorResult(names, f"html:{key}")

        for block in re.findall(
            r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.I | re.S
        ):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            for node in data if isinstance(data, list) else [data]:
                authors = node.get("author") if isinstance(node, dict) else None
                if isinstance(authors, dict):
                    authors = [authors]
                if isinstance(authors, list):
                    names = unique_names(
                        clean_name(a.get("name", "") if isinstance(a, dict) else a)
                        for a in authors
                    )
                    names = [n for n in names if is_plausible_person(n)]
                    if names:
                        return AuthorResult(names, "html:json-ld")
        return AuthorResult()

    # -- 3. DOI -------------------------------------------------------------- #

    @staticmethod
    def _doi_in(text: str) -> str:
        match = DOI_PATTERN.search(text or "")
        if not match:
            return ""
        # Trailing punctuation and file extensions are never part of a DOI.
        return re.sub(r"(?:\.pdf|[).,;\]])+$", "", match.group(1), flags=re.I)

    def _from_crossref_doi(self, doi: str) -> AuthorResult:
        data = self._get_json(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
        message = (data or {}).get("message") or {}
        names = unique_names(_crossref_name(a) for a in message.get("author") or [])
        names = [n for n in names if is_plausible_creator(n)]
        return AuthorResult(names, "crossref:doi") if names else AuthorResult()

    # -- 4. PDF -------------------------------------------------------------- #

    @staticmethod
    def _maybe_pdf(url: str) -> bool:
        path = urlparse(url).path.lower()
        return path.endswith(".pdf") or "/pdf" in path or "fulltext" in path or "getpdf" in url.lower()

    def _download_pdf(self, url: str) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/pdf,*/*;q=0.8",
        }
        with self.session.get(
            url, headers=headers, timeout=PDF_TIMEOUT, stream=True, allow_redirects=True
        ) as response:
            if response.status_code != 200:
                return b""
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > PDF_MAX_BYTES:
                return b""
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > PDF_MAX_BYTES:
                    return b""
        data = b"".join(chunks)
        return data if data[:1024].lstrip().startswith(b"%PDF") or b"%PDF" in data[:1024] else b""

    def _from_pdf(self, url: str) -> tuple[str, AuthorResult]:
        if not _HAVE_PYPDF:
            return "", AuthorResult()
        try:
            data = self._download_pdf(url)
            if not data:
                return "", AuthorResult()
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n".join(
                (page.extract_text() or "") for page in reader.pages[:PDF_TEXT_PAGES]
            )
        except Exception as exc:  # corrupt, encrypted, truncated PDFs
            LOGGER.debug("Could not read PDF %s: %s", url, exc)
            return "", AuthorResult()

        doi = self._doi_in(text)
        if doi:
            result = self._from_crossref_doi(doi)
            if result:
                return text, result

        folded = _fold(text)
        candidates: list[str] = []
        try:
            xmp = reader.xmp_metadata
            if xmp is not None and xmp.dc_creator:
                candidates += list(xmp.dc_creator)
        except Exception:
            pass
        info_author = (reader.metadata or {}).get("/Author")
        if info_author:
            candidates += split_name_list(str(info_author))

        # Only trust metadata the document itself corroborates.
        names = unique_names(
            clean_name(c) for c in candidates
            if is_plausible_person(clean_name(c)) and name_in_text(clean_name(c), folded)
        )
        return text, (AuthorResult(names, "pdf-metadata") if names else AuthorResult())

    # -- 5. title search ----------------------------------------------------- #

    def _from_crossref_title(self, title: str) -> AuthorResult:
        data = self._get_json(
            "https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 5, "select": "title,author"},
        )
        for item in ((data or {}).get("message") or {}).get("items") or []:
            candidate = (item.get("title") or [""])[0]
            if title_similarity(title, candidate) >= TITLE_MATCH_MIN:
                names = unique_names(_crossref_name(a) for a in item.get("author") or [])
                names = [n for n in names if is_plausible_creator(n)]
                if names:
                    return AuthorResult(names, "crossref:title")
        return AuthorResult()

    def _from_openalex_title(self, title: str) -> AuthorResult:
        # OpenAlex's filter syntax treats commas and colons as operators.
        query = re.sub(r"[,:|]", " ", title)
        data = self._get_json(
            "https://api.openalex.org/works",
            params={
                "filter": f"title.search:{query}",
                "per_page": 5,
                "select": "display_name,authorships",
            },
        )
        for item in (data or {}).get("results") or []:
            if title_similarity(title, item.get("display_name") or "") >= TITLE_MATCH_MIN:
                names = unique_names(
                    clean_name((a.get("author") or {}).get("display_name", ""))
                    for a in item.get("authorships") or []
                )
                names = [n for n in names if is_plausible_creator(n)]
                if names:
                    return AuthorResult(names, "openalex:title")
        return AuthorResult()

    # -- 6. LLM -------------------------------------------------------------- #

    def _from_llm(self, text: str, title: str) -> AuthorResult:
        excerpt = re.sub(r"\s+", " ", text)[:3500]
        if len(excerpt) < 200:
            return AuthorResult()  # scanned PDF with no text layer
        # Every exclusion below is a mistake observed in testing: press releases
        # attributed to the official they quote, course outlines attributed to
        # their reading list, encyclopedia entries to their series editors.
        prompt = (
            "Below is the start of a document. Return the byline: the person(s) "
            "credited as author of THIS document, or as its editor when it is an "
            "edited volume with no single author. Names must be printed on the page.\n"
            "Do NOT include: people quoted or discussed; authors of works cited, "
            "recommended or listed as readings; journal, series or encyclopedia "
            "editors; reviewers, supervisors, advisors, or people thanked; "
            "publishing, printing or proof-reading staff.\n"
            "Press releases, course outlines, syllabi, web-page printouts and "
            "encyclopedia pages usually have no byline - return an empty list for "
            "them unless an author is explicitly credited. When unsure, return an "
            "empty list: a missing author is acceptable, a wrong one is not.\n"
            'Reply as JSON: {"authors": ["Given Family", ...]}\n\n'
            f"Title (may be approximate): {title}\n\nDocument start:\n{excerpt}"
        )
        try:
            response = self.session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.openai_api_key}"},
                json={
                    "model": OPENAI_MODEL,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=(10, 60),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            proposed = json.loads(content).get("authors") or []
        except Exception as exc:
            LOGGER.debug("LLM author extraction failed: %s", exc)
            return AuthorResult()

        folded = _fold(text)
        # Hallucination guard: every name must be printed in the document.
        cleaned = (tidy_extracted_name(n) for n in proposed if isinstance(n, str))
        names = unique_names(
            n for n in cleaned if is_plausible_person(n) and surname_in_text(n, folded)
        )
        return AuthorResult(names, "llm") if names else AuthorResult()

    # -- plumbing ------------------------------------------------------------ #

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict | None:
        """
        GET a JSON API, retrying once on timeouts / 429 / 5xx.

        The retry matters for correctness, not just coverage: when a DOI lookup
        silently fails the ladder falls through to title search, which can
        return the *volume's* editors instead of the article's authors.
        """
        for attempt in (1, 2):
            try:
                response = self.session.get(
                    url, params=params, headers=self.api_headers, timeout=API_TIMEOUT
                )
                if response.status_code == 200:
                    return response.json()
                LOGGER.debug("GET %s -> HTTP %s", url, response.status_code)
                if response.status_code not in (429, 500, 502, 503, 504):
                    return None
            except requests.exceptions.Timeout as exc:
                LOGGER.debug("GET %s timed out: %s", url, exc)
            except (requests.exceptions.RequestException, ValueError) as exc:
                LOGGER.debug("GET %s failed: %s", url, exc)
                return None
            if attempt == 1:
                time.sleep(2.0)
        return None
