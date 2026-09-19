"""Prompt-token estimates for models that expose no tokenizer.

A hosted endpoint reports exact ``prompt_tokens`` after each exchange but
offers nothing before it. This keeps a characters-per-token ratio that
starts at a caller-supplied default and is re-calibrated from every
reported usage, so the estimate tracks the actual tokenizer and the
actual prompt mix. Estimates lean high on purpose: a context-window check
that fires early costs a rollover; one that fires late costs the turn.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CHARACTERS_PER_TOKEN = 3.6
DEFAULT_MESSAGE_OVERHEAD_TOKENS = 4
DEFAULT_IMAGE_TOKENS = 1600
DEFAULT_SAFETY_MARGIN = 1.10
MINIMUM_RATIO = 1.0
MAXIMUM_RATIO = 8.0


@dataclass(frozen=True)
class Estimate:
    tokens: int
    characters: int
    ratio: float
    images: int = 0
    calibrated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"tokens": self.tokens, "characters": self.characters, "ratio": round(self.ratio, 3),
                "images": self.images, "calibrated": self.calibrated, "method": "estimate"}


def prompt_characters(payload: dict[str, Any]) -> tuple[int, int, int]:
    """(characters, message count, image count) of a Chat Completions payload.

    Tool definitions and tool-call arguments count as their JSON text, since
    that is roughly how they reach the model.
    """
    characters = 0
    images = 0
    messages = payload.get("messages") or []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            characters += len(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    images += 1
                else:
                    characters += len(str(part.get("text") or ""))
        for extra in ("reasoning_content", "name", "tool_call_id"):
            if message.get(extra):
                characters += len(str(message[extra]))
        for call in message.get("tool_calls") or []:
            characters += len(json.dumps(call, separators=(",", ":")))
    for tool in payload.get("tools") or []:
        characters += len(json.dumps(tool, separators=(",", ":")))
    return characters, len(messages), images


@dataclass
class HostedTokenEstimator:
    """Estimate now, calibrate later.

    ``characters_per_token`` is the starting ratio. ``calibrate`` takes a
    payload and the ``prompt_tokens`` the provider reported for it and moves
    the ratio toward the observed one, weighted by ``calibration_weight``
    (1.0 replaces it outright, 0.0 ignores the sample).
    """

    characters_per_token: float = DEFAULT_CHARACTERS_PER_TOKEN
    message_overhead_tokens: int = DEFAULT_MESSAGE_OVERHEAD_TOKENS
    image_tokens: int = DEFAULT_IMAGE_TOKENS
    safety_margin: float = DEFAULT_SAFETY_MARGIN
    calibration_weight: float = 0.5
    samples: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.characters_per_token <= 0 or self.safety_margin < 1.0 or not 0.0 <= self.calibration_weight <= 1.0:
            raise ValueError("ratio must be positive, margin >= 1, weight within [0, 1]")

    def estimate(self, payload: dict[str, Any]) -> Estimate:
        characters, messages, images = prompt_characters(payload)
        text_tokens = characters / self.characters_per_token + messages * self.message_overhead_tokens
        tokens = int(text_tokens * self.safety_margin) + images * self.image_tokens
        return Estimate(tokens, characters, self.characters_per_token, images, self.samples > 0)

    def calibrate(self, payload: dict[str, Any], prompt_tokens: int) -> float:
        """Fold one observed count in; returns the new ratio."""
        if prompt_tokens <= 0:
            return self.characters_per_token
        characters, messages, images = prompt_characters(payload)
        text_tokens = prompt_tokens - images * self.image_tokens - messages * self.message_overhead_tokens
        if text_tokens <= 0 or characters <= 0:
            return self.characters_per_token
        observed = min(max(characters / text_tokens, MINIMUM_RATIO), MAXIMUM_RATIO)
        weight = self.calibration_weight if self.samples else 1.0
        self.characters_per_token = self.characters_per_token * (1 - weight) + observed * weight
        self.samples += 1
        return self.characters_per_token
