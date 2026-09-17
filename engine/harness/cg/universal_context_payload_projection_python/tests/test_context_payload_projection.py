from __future__ import annotations

from src.context_payload_projection import compact_text_excerpt
from src.context_payload_projection import project_payload_content


def test_project_payload_content_keeps_unknown_decision() -> None:
    result = project_payload_content("keep this", projection={"decision": "keep"})

    assert result.content == "keep this"
    assert result.decision == "keep"
    assert result.replaced is False


def test_project_payload_content_drops_with_metadata_marker() -> None:
    result = project_payload_content(
        "large payload",
        projection={
            "decision": "drop",
            "reason": "old_large",
            "name": "read_file",
            "status": "success",
            "token_count": 1200,
        },
    )

    assert result.replaced is True
    assert result.content == "[content omitted: read_file, success, 1200 tokens, reason: old_large, available in session log]"


def test_project_payload_content_summarizes_with_bounded_excerpt() -> None:
    result = project_payload_content(
        "\n".join(f"line {index}" for index in range(100)),
        projection={
            "decision": "summarize",
            "reason": "old_large",
            "name": "search",
            "status": "success",
            "token_count": 3000,
        },
        max_summary_chars=120,
    )

    assert result.replaced is True
    assert result.content.startswith("[content summarized: search, success, 3000 tokens, reason: old_large, available in session log]")
    assert "line 0" in result.content
    assert "middle omitted" in result.content
    assert "line 99" in result.content
    assert len(result.content) < 260


def test_project_payload_content_prefers_distilled_summary_text() -> None:
    result = project_payload_content(
        "raw payload that should not be shown",
        projection={
            "decision": "summarize",
            "reason": "distilled",
            "name": "read_file",
            "status": "success",
            "summary_text": "Important retained facts only.",
            "token_count": 2000,
        },
    )

    assert "Important retained facts only." in result.content
    assert "raw payload" not in result.content


def test_compact_text_excerpt_normalizes_outer_blank_lines() -> None:
    assert compact_text_excerpt("\n\n  hello  \n\n", max_chars=80) == "  hello"
