"""Input sanitization, token budgeting, and retrieval-scope policy for Ask.

Everything user-supplied or corpus-supplied passes through here *before* it is
placed into a prompt template, so the templates can rely on two invariants:

  1. no control characters or zero-width homoglyph tricks survive, and
  2. the reserved isolation delimiters (``<retrieved_sources>``) can never be
     opened or closed by untrusted text — only the template itself emits them.
"""
from __future__ import annotations

import re
import unicodedata

# Reserved isolation tags emitted only by ask_context_template.md.
_RESERVED_TAG_RE = re.compile(r"</?\s*retrieved_sources\s*>", re.IGNORECASE)
# Common LLM chat-scaffold forgeries that have no business in a student
# question or a history document; defanged rather than deleted so the text
# remains readable evidence.
_SCAFFOLD_RE = re.compile(
    r"</?\s*(system|assistant|instructions?)\s*>|\[/?(SYSTEM|INST)\]|<\|[a-z_]+\|>",
    re.IGNORECASE,
)
# Zero-width and BOM-class characters used to smuggle text past filters.
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")

ALLOWED_SCOPES: frozenset[str] = frozenset({"ask_soakingarri"})

APPROX_CHARS_PER_TOKEN = 4


class PolicyViolation(ValueError):
    """Raised when input fails validation before any retrieval/LLM work."""


def sanitize_text(text: str) -> str:
    """Neutralize injection vectors in untrusted text (user prompt or corpus).

    Normalizes unicode, strips control/zero-width characters, and defangs the
    reserved isolation tags and chat-scaffold markers by bracketing them so
    they read as quoted text instead of parsing as structure.
    """
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = "".join(
        ch for ch in text if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C"
    )
    text = _RESERVED_TAG_RE.sub("[quoted-tag]", text)
    text = _SCAFFOLD_RE.sub("[quoted-tag]", text)
    # Collapse pathological whitespace runs while preserving paragraph breaks.
    text = re.sub(r"[ \t]{3,}", "  ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Cheap provider-agnostic token estimate (~4 chars/token)."""
    return max(1, len(text) // APPROX_CHARS_PER_TOKEN)


def validate_prompt(prompt: str, *, max_tokens: int) -> str:
    """Sanitize a user prompt and enforce the token budget.

    Returns the sanitized prompt; raises :class:`PolicyViolation` when the
    result is empty or over budget.
    """
    cleaned = sanitize_text(prompt)
    if not cleaned:
        raise PolicyViolation("Prompt is empty after sanitization.")
    if estimate_tokens(cleaned) > max_tokens:
        raise PolicyViolation(
            f"Prompt exceeds the {max_tokens}-token budget; shorten the question."
        )
    return cleaned


def validate_scope(scope: str) -> str:
    """Restrict retrieval to explicitly allowlisted corpora."""
    if scope not in ALLOWED_SCOPES:
        raise PolicyViolation(f"Retrieval scope {scope!r} is not allowed.")
    return scope
