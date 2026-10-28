"""Centralized AI inference service.

Single entry point for every subdomain's LLM needs:
  * text completion + streaming
  * strict structured JSON output with schema validation
  * embeddings for the pgvector RAG pipeline
  * retry with exponential backoff
  * per-call token usage analytics

Providers: Amazon Bedrock, the Anthropic API, or the OpenAI API — selected by
``AI_PROVIDER``. The public interface is provider-agnostic; provider SDKs are
imported lazily so only the configured one needs to be installed.

Bedrock goes through the Converse API, which speaks one request shape for every
model it hosts, so switching between DeepSeek and Claude is an ``AI_MODEL``
change rather than a code change.

Embeddings are pinned separately by ``EMBEDDING_PROVIDER``: every vector in the
RAG index must come from the same model as the query vectors, so changing the
chat provider must never silently change the embedding model.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Model families that accept text only. Image parts sent to them are dropped
# (Bedrock would otherwise reject the whole request), and callers check
# ``ai_service.supports_images`` so the prompt never claims a picture is there.
_TEXT_ONLY_MODEL_MARKERS = ("deepseek",)


class AIServiceError(Exception):
    """Raised when inference fails after all retries or JSON cannot be validated."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AIResult:
    text: str
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict)


def _build_client(provider: str) -> Any:
    if provider == "bedrock":
        import boto3
        from botocore.config import Config

        return boto3.client(
            "bedrock-runtime",
            region_name=settings.BEDROCK_REGION,
            config=Config(
                connect_timeout=10,
                read_timeout=settings.AI_TIMEOUT_SECONDS,
                # Tenacity owns retries; botocore's own would multiply them.
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )
    if provider == "openai":
        from openai import OpenAI

        return OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
    # anthropic
    from anthropic import Anthropic

    return Anthropic(api_key=settings.ANTHROPIC_API_KEY)


class AIService:
    def __init__(self) -> None:
        self.provider = settings.AI_PROVIDER
        self.model = settings.AI_MODEL
        self.supports_images = not any(
            marker in self.model.lower() for marker in _TEXT_ONLY_MODEL_MARKERS
        )
        self._client = _build_client(self.provider)
        self._embed_client: Any = None

    # ------------------------------------------------------------------ #
    # Core completion
    # ------------------------------------------------------------------ #
    @retry(
        reraise=True,
        stop=stop_after_attempt(settings.AI_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type(AIServiceError),
    )
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int = 1500,
        temperature: float = 0.7,
    ) -> AIResult:
        """Run a chat completion and return text + token usage."""
        if self.provider == "bedrock":
            call = self._complete_bedrock
        elif self.provider == "openai":
            call = self._complete_openai
        else:
            call = self._complete_anthropic
        try:
            # The provider SDKs are synchronous; a worker thread keeps a slow
            # completion from stalling every other request on the event loop.
            return await asyncio.to_thread(call, system, messages, max_tokens, temperature)
        except AIServiceError:
            raise
        except Exception as exc:  # network, throttling, etc. -> retryable
            raise AIServiceError(str(exc)) from exc

    def _complete_anthropic(
        self, system: str, messages: list[dict], max_tokens: int, temp: float
    ) -> AIResult:
        resp = self._client.messages.create(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            temperature=temp,
            messages=_to_anthropic_messages(messages),
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return AIResult(text=text, usage=usage, raw=resp.model_dump())

    def _complete_openai(
        self, system: str, messages: list[dict], max_tokens: int, temp: float
    ) -> AIResult:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                *_to_openai_messages(messages),
            ],
            max_completion_tokens=max_tokens,
            temperature=temp,
        )
        text = resp.choices[0].message.content or ""
        usage = Usage(
            resp.usage.prompt_tokens if resp.usage else 0,
            resp.usage.completion_tokens if resp.usage else 0,
        )
        return AIResult(text=text, usage=usage, raw=resp.model_dump())

    def _complete_bedrock(
        self, system: str, messages: list[dict], max_tokens: int, temp: float
    ) -> AIResult:
        request: dict[str, Any] = {
            "modelId": self.model,
            "messages": _to_converse_messages(messages, allow_images=self.supports_images),
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temp},
        }
        if system:
            request["system"] = [{"text": system}]
        resp = self._client.converse(**request)

        # Reasoning models (DeepSeek R1) return a ``reasoningContent`` block next
        # to the answer; only the ``text`` blocks are the answer itself.
        blocks = resp.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b["text"] for b in blocks if "text" in b).strip()
        if resp.get("stopReason") == "max_tokens":
            logger.warning(
                "Bedrock %s hit max_tokens=%d; the answer may be cut short",
                self.model,
                max_tokens,
            )
        usage_block = resp.get("usage", {})
        usage = Usage(usage_block.get("inputTokens", 0), usage_block.get("outputTokens", 0))
        return AIResult(text=text, usage=usage, raw=resp)

    # ------------------------------------------------------------------ #
    # Structured JSON output with schema validation
    # ------------------------------------------------------------------ #
    async def complete_json(
        self,
        *,
        system: str,
        messages: list[dict],
        schema: type[T],
        max_tokens: int = 1500,
        temperature: float = 0.4,
    ) -> tuple[T, Usage]:
        """Force a JSON response and validate it against a pydantic ``schema``.

        The schema's JSON contract is appended to the system prompt, the model
        output is parsed, and validation errors trigger the retry loop.
        """
        contract = (
            f"{system}\n\nYou MUST respond with ONLY valid JSON matching this schema, "
            f"no prose, no markdown fences:\n{json.dumps(schema.model_json_schema())}"
        )

        @retry(
            reraise=True,
            stop=stop_after_attempt(settings.AI_MAX_RETRIES),
            wait=wait_exponential(multiplier=1, min=1, max=15),
            retry=retry_if_exception_type((AIServiceError, ValidationError)),
        )
        async def _attempt() -> tuple[T, Usage]:
            result = await self.complete(
                system=contract,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            parsed = schema.model_validate_json(_strip_fences(result.text))
            return parsed, result.usage

        try:
            return await _attempt()
        except (AIServiceError, ValidationError) as exc:
            raise AIServiceError(f"Structured output failed: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Embeddings (RAG)
    # ------------------------------------------------------------------ #
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one ``EMBEDDING_DIM``-wide vector per input text."""
        if settings.EMBEDDING_PROVIDER == "openai":
            return await asyncio.to_thread(self._embed_openai, texts)
        return await asyncio.to_thread(self._embed_bedrock, texts)

    def _embedding_client(self) -> Any:
        if self._embed_client is None:
            same = settings.EMBEDDING_PROVIDER == self.provider
            self._embed_client = (
                self._client if same else _build_client(settings.EMBEDDING_PROVIDER)
            )
        return self._embed_client

    def _embed_bedrock(self, texts: list[str]) -> list[list[float]]:
        client = self._embedding_client()
        vectors: list[list[float]] = []
        for text in texts:
            # Titan v2 takes one text per call. The width is requested
            # explicitly so it always matches the pgvector column.
            resp = client.invoke_model(
                modelId=settings.AI_EMBEDDING_MODEL,
                body=json.dumps(
                    {
                        "inputText": text,
                        "dimensions": settings.EMBEDDING_DIM,
                        "normalize": True,
                    }
                ),
            )
            vectors.append(json.loads(resp["body"].read())["embedding"])
        return vectors

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        # ``dimensions`` shortens text-embedding-3 vectors to match the
        # pgvector column width configured for the platform.
        resp = self._embedding_client().embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=texts,
            dimensions=settings.EMBEDDING_DIM,
        )
        return [item.embedding for item in resp.data]


# --------------------------------------------------------------------------- #
# Multimodal message translation
# --------------------------------------------------------------------------- #
# Callers build messages in one provider-neutral shape. ``content`` is either a
# plain string or a list of parts:
#
#   {"type": "text",  "text": "..."}
#   {"type": "image", "media_type": "image/png", "data": "<base64>"}
#
# Each provider adapter below translates that into its own wire format, so no
# caller needs to know which provider is configured.
def _to_openai_messages(messages: list[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            converted.append(message)
            continue
        parts: list[dict] = []
        for part in content or []:
            if part.get("type") == "image":
                data_url = f"data:{part['media_type']};base64,{part['data']}"
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            else:
                parts.append({"type": "text", "text": part.get("text", "")})
        converted.append({"role": message["role"], "content": parts})
    return converted


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            converted.append(message)
            continue
        parts: list[dict] = []
        for part in content or []:
            if part.get("type") == "image":
                parts.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part["media_type"],
                            "data": part["data"],
                        },
                    }
                )
            else:
                parts.append({"type": "text", "text": part.get("text", "")})
        converted.append({"role": message["role"], "content": parts})
    return converted


def _to_converse_messages(messages: list[dict], *, allow_images: bool) -> list[dict]:
    """Translate to Bedrock Converse blocks: ``{"text"}`` and ``{"image"}``.

    Converse rejects blank text blocks, and a blank assistant turn in the
    history (e.g. a reasoning model that spent its whole budget thinking) would
    otherwise break every later turn of that conversation.
    """
    converted: list[dict] = []
    for message in messages:
        content = message.get("content")
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        blocks: list[dict] = []
        for part in parts or []:
            if part.get("type") == "image":
                if allow_images:
                    fmt = part["media_type"].split("/")[-1].replace("jpg", "jpeg")
                    blocks.append(
                        {
                            "image": {
                                "format": fmt,
                                "source": {"bytes": base64.b64decode(part["data"])},
                            }
                        }
                    )
            elif (part.get("text") or "").strip():
                blocks.append({"text": part["text"]})
        converted.append(
            {"role": message["role"], "content": blocks or [{"text": "(empty message)"}]}
        )
    return converted


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


# Process-wide singleton (client connection pools are reused).
ai_service = AIService()
