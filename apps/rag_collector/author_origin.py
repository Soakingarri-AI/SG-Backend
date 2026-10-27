"""
author_origin.py
================

Fills column G, `author_is_african`: is this document's author African?

What this can and cannot do
---------------------------
There is no lookup table for this. The model is asked about the author it has
been given, with the document's title and host as context, and answers TRUE or
FALSE - or UNKNOWN, which is the important one.

Names are weak evidence of origin. African authors carry Arabic, Portuguese,
French, English and Afrikaans names; plenty of non-African authors have names
that read as African; and a surname alone rarely settles it. So the prompt asks
about the author as a person - who they are, where they work - and requires
UNKNOWN whenever that would be guesswork. A blank cell is a correct answer here,
and far more useful than a confident wrong one.

The column is a strong hint for filtering, not a fact about anybody.

Caching
-------
Verdicts are cached per author for the life of the process, so a prolific author
costs one call however many documents they wrote. A failed call is not cached:
it says nothing about the author, and the next document by them asks again.

Pass `cache_path` to keep verdicts across runs (backfill_origin.py does). That
matters for the unsure ones: the sheet stores them as a blank, the same as
"never asked", so without the file an hourly backfill would re-ask thousands of
authors the model already declined to judge.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import requests

from llm_client import LLMClient

LOGGER = logging.getLogger("dork_collector.origin")

API_TIMEOUT = 45
MAX_CACHE = 50_000

#: Column G values. "" means "not judged": no author, or the model was unsure.
TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", ""

#: Author strings that are not a person, so there is nothing to judge.
NOT_A_PERSON = ("et al.", "unknown", "anonymous")

PROMPT = """You are told the author(s) of a document, with the document's title \
and the site hosting it as context.

Question: is the author African - born in, or a national of, an African country?
When several authors are listed, answer about the first one only.

Reply as JSON: {"verdict": "TRUE" | "FALSE" | "UNKNOWN", "why": "<8 words>"}

- TRUE: you recognise this person as African, or the context shows it clearly \
(for example an African university affiliation).
- FALSE: you recognise this person, and they are not African.
- UNKNOWN: anything else.

Answer UNKNOWN whenever you would be guessing from the name. Names travel: \
Arabic, Portuguese, French, English and Afrikaans names are common across \
Africa, and names that sound African belong to people everywhere. A wrong \
answer is worse than UNKNOWN. Never infer from the subject matter - scholars of \
every origin write about African history. An organisation is not a person: \
answer UNKNOWN for one."""


@dataclass
class OriginVerdict:
    value: str = UNKNOWN     # TRUE / FALSE / ""
    why: str = ""

    def __bool__(self) -> bool:
        return self.value in (TRUE, FALSE)


class AfricanAuthorClassifier:
    """
    Thread-safe. `classify()` never raises: any failure is UNKNOWN.

    With no LLM backend available (see llm_client.py) it is simply disabled, so
    the collector runs unchanged on a box without one and column G stays blank.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        llm: LLMClient | None = None,
        enabled: bool = True,
        cache_path: Path | None = None,
    ) -> None:
        self.llm = llm or LLMClient(session=session or requests.Session())
        self.enabled = enabled and self.llm.enabled
        self.cache_path = cache_path
        self._cache: dict[str, OriginVerdict] = self._load_cache()
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0

    def classify(
        self, authors: str, title: str = "", source: str = "", url: str = ""
    ) -> OriginVerdict:
        """Judge the first-named author in `authors` ("A; B; C")."""
        first = self.first_author(authors)
        if not first or not self.enabled:
            return OriginVerdict()

        key = first.lower()
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        verdict = self._ask(first, authors, title, source, url)
        if verdict is None:              # the call failed; ask again next time
            return OriginVerdict()
        with self._lock:
            if len(self._cache) < MAX_CACHE:
                self._cache[key] = verdict
        return verdict

    def is_judged(self, authors: str) -> bool:
        """True when this row's first author already has a cached verdict."""
        first = self.first_author(authors)
        with self._lock:
            return bool(first) and first.lower() in self._cache

    @staticmethod
    def first_author(authors: str) -> str:
        first = re.split(r"\s*;\s*", str(authors or "").strip())[0].strip()
        return "" if first.lower() in NOT_A_PERSON else first

    # -- persistence --------------------------------------------------------- #

    def _load_cache(self) -> dict[str, OriginVerdict]:
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return {k: OriginVerdict(v[0], v[1]) for k, v in raw.get("verdicts", {}).items()}
        except (OSError, ValueError, TypeError, IndexError) as exc:
            LOGGER.warning("Ignoring unreadable origin cache %s: %s", self.cache_path, exc)
            return {}

    def save_cache(self) -> None:
        """Write the verdicts to `cache_path` atomically (no-op without one)."""
        if not self.cache_path:
            return
        with self._lock:
            data = {
                "model": self.llm.describe(),
                "verdicts": {k: [v.value, v.why] for k, v in self._cache.items()},
            }
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.cache_path)

    # -- model --------------------------------------------------------------- #

    def _ask(
        self, author: str, authors: str, title: str, source: str, url: str
    ) -> OriginVerdict | None:
        # Co-authors and the full URL (an institutional repository path, a
        # journal's site) are context the model can recognise people by.
        # Measured on 45 authors gpt-4o-mini had judged: DeepSeek agreed on
        # 23/30 TRUE with them vs 20/30 without, and never flipped a verdict.
        # The topic is deliberately not sent: subject matter is not evidence.
        content = f"Author: {author}\n"
        if authors and authors.strip() != author:
            content += f"All authors: {authors[:300]}\n"
        content += f"Document title: {title[:200]}\nHosted on: {source}"
        if url:
            content += f"\nDocument URL: {url[:200]}"
        try:
            payload = self.llm.json_chat(content, system=PROMPT, timeout=API_TIMEOUT)
            self.calls += 1
        except Exception as exc:
            LOGGER.debug("Origin lookup failed for %r: %s", author, exc)
            self.failures += 1
            return None

        verdict = str(payload.get("verdict", "")).strip().upper()
        why = re.sub(r"\s+", " ", str(payload.get("why", ""))).strip()[:80]
        return OriginVerdict(verdict if verdict in (TRUE, FALSE) else UNKNOWN, why)
