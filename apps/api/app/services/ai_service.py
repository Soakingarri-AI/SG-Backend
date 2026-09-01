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
"""
from __future__ import annotations

import json
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

T = TypeVar("T", bound=BaseModel)


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


class AIService:
    def __init__(self) -> None:
        self.provider = settings.AI_PROVIDER
        self.model = settings.AI_MODEL
        self._client = self._build_client()

    # ------------------------------------------------------------------ #
    # Client wiring
    # ------------------------------------------------------------------ #
    def _build_client(self) -> Any:
        if self.provider == "bedrock":
            import boto3

            return boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
        if self.provider == "openai":
            from openai import OpenAI

            return OpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=settings.AI_TIMEOUT_SECONDS,
            )
        # anthropic
        from anthropic import Anthropic

        return Anthropic(api_key=settings.ANTHROPIC_API_KEY)

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
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
        temperature: float = 0.7,
    ) -> AIResult:
        """Run a chat completion and return text + token usage."""
        try:
            if self.provider == "bedrock":
                return self._complete_bedrock(system, messages, max_tokens, temperature)
            if self.provider == "openai":
                return self._complete_openai(system, messages, max_tokens, temperature)
            return self._complete_anthropic(system, messages, max_tokens, temperature)
        except AIServiceError:
            raise
        except Exception as exc:  # network, throttling, etc. -> retryable
            raise AIServiceError(str(exc)) from exc

    def _complete_anthropic(
        self, system: str, messages: list[dict[str, str]], max_tokens: int, temp: float
    ) -> AIResult:
        resp = self._client.messages.create(
            model=self.model,
            system=system,
            max_tokens=max_tokens,
            temperature=temp,
            messages=messages,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = Usage(resp.usage.input_tokens, resp.usage.output_tokens)
        return AIResult(text=text, usage=usage, raw=resp.model_dump())

    def _complete_openai(
        self, system: str, messages: list[dict[str, str]], max_tokens: int, temp: float
    ) -> AIResult:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
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
        self, system: str, messages: list[dict[str, str]], max_tokens: int, temp: float
    ) -> AIResult:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": messages,
        }
        resp = self._client.invoke_model(
            modelId=self.model, body=json.dumps(body)
        )
        payload = json.loads(resp["body"].read())
        text = "".join(
            b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
        )
        usage_block = payload.get("usage", {})
        usage = Usage(
            usage_block.get("input_tokens", 0), usage_block.get("output_tokens", 0)
        )
        return AIResult(text=text, usage=usage, raw=payload)

    # ------------------------------------------------------------------ #
    # Structured JSON output with schema validation
    # ------------------------------------------------------------------ #
    async def complete_json(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
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
        """Return one embedding vector per input text."""
        if self.provider == "bedrock":
            vectors: list[list[float]] = []
            for text in texts:
                resp = self._client.invoke_model(
                    modelId=settings.AI_EMBEDDING_MODEL,
                    body=json.dumps({"inputText": text}),
                )
                payload = json.loads(resp["body"].read())
                vectors.append(payload["embedding"])
            return vectors
        if self.provider == "openai":
            # ``dimensions`` shortens text-embedding-3 vectors to match the
            # pgvector column width configured for the platform.
            resp = self._client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=texts,
                dimensions=settings.EMBEDDING_DIM,
            )
            return [item.embedding for item in resp.data]
        # Anthropic has no first-party embedding endpoint; production uses Bedrock
        # Titan. Dev fallback returns a deterministic zero vector for wiring tests.
        return [[0.0] * settings.EMBEDDING_DIM for _ in texts]


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


# Process-wide singleton (client connection pools are reused).
ai_service = AIService()
