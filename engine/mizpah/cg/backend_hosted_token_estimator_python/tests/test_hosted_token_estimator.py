import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.hosted_token_estimator import HostedTokenEstimator, prompt_characters  # noqa: E402

PAYLOAD = {
    "messages": [
        {"role": "system", "content": "a" * 100},
        {"role": "user", "content": [{"type": "text", "text": "b" * 50}, {"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "c" * 10},
    ],
    "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}],
}


def test_prompt_characters_counts_text_tools_and_images() -> None:
    characters, messages, images = prompt_characters(PAYLOAD)
    assert messages == 4 and images == 1
    assert characters > 160  # text plus JSON of the tool call, tool_call_id and tool definition


def test_estimate_is_conservative_and_uncalibrated_at_first() -> None:
    estimator = HostedTokenEstimator(characters_per_token=4.0, message_overhead_tokens=0, image_tokens=100, safety_margin=1.0)
    estimate = estimator.estimate({"messages": [{"role": "user", "content": "x" * 400}]})
    assert estimate.tokens == 100 and estimate.images == 0 and not estimate.calibrated
    with_image = estimator.estimate({"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]})
    assert with_image.tokens == 100 and with_image.images == 1
    margin = HostedTokenEstimator(characters_per_token=4.0, message_overhead_tokens=0, safety_margin=1.5)
    assert margin.estimate({"messages": [{"role": "user", "content": "x" * 400}]}).tokens == 150
    assert estimate.as_dict()["method"] == "estimate"


def test_calibration_moves_ratio_toward_observed() -> None:
    estimator = HostedTokenEstimator(characters_per_token=4.0, message_overhead_tokens=0, safety_margin=1.0, calibration_weight=0.5)
    payload = {"messages": [{"role": "user", "content": "x" * 300}]}
    assert estimator.calibrate(payload, 100) == pytest.approx(3.0)  # first sample replaces
    assert estimator.calibrate(payload, 150) == pytest.approx(2.5)  # then half-way
    assert estimator.estimate(payload).calibrated
    assert estimator.estimate(payload).tokens == 120


def test_calibration_ignores_bad_samples_and_clamps() -> None:
    estimator = HostedTokenEstimator(characters_per_token=4.0, message_overhead_tokens=0, image_tokens=1000)
    payload = {"messages": [{"role": "user", "content": "x" * 100}]}
    assert estimator.calibrate(payload, 0) == 4.0
    assert estimator.calibrate({"messages": []}, 10) == 4.0
    image_only = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]}
    assert estimator.calibrate(image_only, 500) == 4.0
    assert estimator.calibrate(payload, 1) == 8.0
    assert estimator.calibrate(payload, 100000) == pytest.approx(4.5)


def test_rejects_bad_parameters() -> None:
    for kwargs in ({"characters_per_token": 0}, {"safety_margin": 0.9}, {"calibration_weight": 2}):
        with pytest.raises(ValueError):
            HostedTokenEstimator(**kwargs)
