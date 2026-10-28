"""Bedrock Converse adapter: request shape and response parsing.

A fake client stands in for ``bedrock-runtime`` so these run without AWS. The
live models were checked by hand against the real API (DeepSeek V3.2, DeepSeek
R1, Titan v2); these tests pin the translation those calls depend on.
"""
from __future__ import annotations

import base64

from app.services.ai_service import AIService, _to_converse_messages


class FakeBedrock:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] = []

    def converse(self, **request) -> dict:
        self.requests.append(request)
        return self.response


def _service(client: FakeBedrock, *, model: str = "deepseek.v3.2") -> AIService:
    svc = AIService.__new__(AIService)  # skip building a real boto3 client
    svc.provider = "bedrock"
    svc.model = model
    svc.supports_images = "deepseek" not in model
    svc._client = client
    svc._embed_client = None
    return svc


def test_converse_request_and_reasoning_block_is_skipped() -> None:
    """R1 answers with a reasoning block beside the text; only text is kept."""
    fake = FakeBedrock(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"reasoningContent": {"reasoningText": {"text": "Let me think"}}},
                        {"text": "\n\nSundiata Keita founded Mali."},
                    ],
                }
            },
            "stopReason": "end_turn",
            "usage": {"inputTokens": 26, "outputTokens": 135, "totalTokens": 161},
        }
    )
    result = _service(fake, model="us.deepseek.r1-v1:0")._complete_bedrock(
        "Be terse.", [{"role": "user", "content": "Who founded Mali?"}], 400, 0.3
    )

    assert result.text == "Sundiata Keita founded Mali."
    assert (result.usage.prompt_tokens, result.usage.completion_tokens) == (26, 135)

    request = fake.requests[0]
    assert request["modelId"] == "us.deepseek.r1-v1:0"
    assert request["system"] == [{"text": "Be terse."}]
    assert request["inferenceConfig"] == {"maxTokens": 400, "temperature": 0.3}
    assert request["messages"] == [
        {"role": "user", "content": [{"text": "Who founded Mali?"}]}
    ]


def test_blank_system_prompt_is_omitted() -> None:
    """Converse rejects an empty system text block."""
    fake = FakeBedrock({"output": {"message": {"content": [{"text": "ok"}]}}})
    _service(fake)._complete_bedrock("", [{"role": "user", "content": "hi"}], 10, 0.0)
    assert "system" not in fake.requests[0]


def test_converse_images_are_bytes_or_dropped_for_text_only_models() -> None:
    neutral = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image", "media_type": "image/jpg", "data": "QUJD"},
            ],
        }
    ]

    with_images = _to_converse_messages(neutral, allow_images=True)[0]["content"]
    assert with_images[0] == {"text": "what is this"}
    assert with_images[1] == {
        "image": {"format": "jpeg", "source": {"bytes": base64.b64decode("QUJD")}}
    }

    text_only = _to_converse_messages(neutral, allow_images=False)[0]["content"]
    assert text_only == [{"text": "what is this"}]


def test_blank_history_turn_cannot_poison_the_conversation() -> None:
    """A stored empty assistant reply would make Converse reject every later turn."""
    converted = _to_converse_messages(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "second"},
        ],
        allow_images=False,
    )
    assert converted[1] == {"role": "assistant", "content": [{"text": "(empty message)"}]}
    assert [m["role"] for m in converted] == ["user", "assistant", "user"]
