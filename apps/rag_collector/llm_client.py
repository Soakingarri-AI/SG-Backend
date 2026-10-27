"""
llm_client.py
=============

One small JSON-chat helper for the collector's two model calls: reading a PDF's
byline (author_resolver.py, column F) and judging author origin
(author_origin.py, column G).

Backends, chosen by `LLM_PROVIDER`:

    bedrock (default)  DeepSeek V3.2 on Amazon Bedrock, via the Converse API.
                       Credentials come from the standard AWS chain: AWS_PROFILE
                       locally, the EC2 instance role on the box.
    openai             gpt-4o-mini over the OpenAI REST API (OPENAI_API_KEY).

`LLM_MODEL` overrides the model id for whichever backend is in use.

When Bedrock is selected but no AWS credentials can be found and an OpenAI key
is set, the client falls back to OpenAI and says so in the log. That keeps the
collector box (which has no instance role yet) filling columns F and G instead
of silently leaving them blank, and it switches to DeepSeek on its own once the
box can reach Bedrock.

Thread-safe: the collector calls it from its verification workers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

import requests

LOGGER = logging.getLogger("dork_collector.llm")

DEFAULT_MODELS = {"bedrock": "deepseek.v3.2", "openai": "gpt-4o-mini"}
BEDROCK_REGION = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
MAX_TOKENS = 500            # replies are small JSON objects

# Bedrock has no JSON mode for DeepSeek, so the instruction is explicit and the
# reply is parsed leniently (fences and stray prose around the object).
_JSON_ONLY = "\n\nRespond with only the JSON object: no prose, no code fences."
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    """The model call failed or its reply was not a JSON object."""


def _aws_credentials_available() -> bool:
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception as exc:  # missing boto3, unknown AWS_PROFILE, ...
        LOGGER.debug("No usable AWS credentials: %s", exc)
        return False


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        session: requests.Session | None = None,
        openai_api_key: str | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        wanted = (provider or os.getenv("LLM_PROVIDER") or "bedrock").strip().lower()

        self.provider = ""      # "" = no usable backend; callers skip the LLM step
        if wanted == "bedrock":
            if _aws_credentials_available():
                self.provider = "bedrock"
            elif self.openai_api_key:
                LOGGER.warning(
                    "LLM_PROVIDER=bedrock but no AWS credentials were found; "
                    "falling back to OpenAI until this machine can reach Bedrock"
                )
                self.provider = "openai"
        elif wanted == "openai" and self.openai_api_key:
            self.provider = "openai"

        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(self.provider, "")
        self._bedrock: Any = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.provider)

    def describe(self) -> str:
        return f"{self.model} on {self.provider}" if self.enabled else "disabled"

    # ------------------------------------------------------------------ #

    def json_chat(self, user: str, system: str = "", timeout: float = 60) -> dict:
        """Send one turn and return the reply parsed as a JSON object."""
        if not self.enabled:
            raise LLMError("no LLM backend is configured")
        if self.provider == "bedrock":
            text = self._bedrock_chat(system, user)
        else:
            text = self._openai_chat(system, user, timeout)
        return _parse_object(text)

    def _bedrock_client(self) -> Any:
        with self._lock:
            if self._bedrock is None:
                import boto3
                from botocore.config import Config

                self._bedrock = boto3.client(
                    "bedrock-runtime",
                    region_name=BEDROCK_REGION,
                    config=Config(
                        connect_timeout=10,
                        read_timeout=60,
                        # Absorbs on-demand throttling when 8 workers call at once.
                        retries={"mode": "standard", "total_max_attempts": 4},
                    ),
                )
            return self._bedrock

    def _bedrock_chat(self, system: str, user: str) -> str:
        request: dict[str, Any] = {
            "modelId": self.model,
            "messages": [{"role": "user", "content": [{"text": user + _JSON_ONLY}]}],
            "inferenceConfig": {"maxTokens": MAX_TOKENS, "temperature": 0},
        }
        if system:
            request["system"] = [{"text": system}]
        try:
            resp = self._bedrock_client().converse(**request)
        except Exception as exc:
            raise LLMError(f"Bedrock {self.model}: {exc}") from exc
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        # Reasoning models return a reasoningContent block beside the answer.
        return "".join(b["text"] for b in blocks if "text" in b)

    def _openai_chat(self, system: str, user: str, timeout: float) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": user})
        try:
            response = self.session.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self.openai_api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                },
                timeout=(10, timeout),
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            raise LLMError(f"OpenAI {self.model}: {exc}") from exc


def _parse_object(text: str) -> dict:
    match = _OBJECT_RE.search(text or "")
    if not match:
        raise LLMError(f"reply had no JSON object: {text[:120]!r}")
    try:
        payload = json.loads(match.group(0))
    except ValueError as exc:
        raise LLMError(f"reply was not valid JSON: {text[:120]!r}") from exc
    if not isinstance(payload, dict):
        raise LLMError("reply was JSON but not an object")
    return payload
