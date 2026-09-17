from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Callable


DECISION_KEEP = "keep"
DECISION_SUMMARIZE = "summarize"
DECISION_DROP = "drop"


@dataclass(frozen=True)
class PayloadProjection:
    content: str
    decision: str
    replaced: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "decision": self.decision,
            "replaced": self.replaced,
            "reason": self.reason,
        }


def project_native_messages(
    messages: list[dict[str, Any]], *, protected_prefix: int,
    max_field_characters: int, archive_reference: str, protected_suffix: int = 0,
) -> dict[str, Any]:
    """Bound dynamic text without losing native call/result identities.

    The caller must durably archive the original messages before using the result.
    Excerpts are explicitly marked, not presented as semantic summaries. Oversized
    arguments become a valid JSON archive pointer, never broken JSON or invented
    original arguments. Fixed system/assignment messages are left intact.
    """
    if (not 0 <= protected_prefix <= len(messages) or max_field_characters < 0 or not archive_reference
            or not 0 <= protected_suffix <= len(messages)-protected_prefix):
        raise ValueError('Invalid native projection bounds or archive reference')
    projected = deepcopy(messages)
    replaced = []

    def bound(value: Any, location: str) -> Any:
        if not isinstance(value, str) or len(value) <= max_field_characters:
            return value
        replaced.append(location)
        notice = f'[Excerpt only; full original: {archive_reference}#{location}]'
        return notice + ('\n'+compact_text_excerpt(value, max_chars=max_field_characters)
                         if max_field_characters else '')

    end = len(projected)-protected_suffix
    for index, message in enumerate(projected[protected_prefix:end], protected_prefix):
        for field in ('content', 'reasoning_content'):
            if field in message:
                message[field] = bound(message[field], f'messages/{index}/{field}')
        for call_index, call in enumerate(message.get('tool_calls') or []):
            function = call['function']
            value = function['arguments']
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if len(rendered) > max_field_characters:
                location = f'messages/{index}/tool_calls/{call_index}/function/arguments'
                replaced.append(location)
                pointer = dict(archived_original_arguments=archive_reference+'#'+location)
                function['arguments'] = json.dumps(pointer) if isinstance(value, str) else pointer
    return dict(messages=projected, replaced_fields=replaced, archive_reference=archive_reference)


def _exchange_ranges(messages: list[dict[str, Any]], start: int) -> list[tuple[int, int]]:
    """Keep every assistant call batch and all its results in one removable unit."""
    groups = []
    while start < len(messages):
        message = messages[start]
        if message.get('role') == 'tool':
            raise ValueError('Cannot fit a conversation with an orphan tool result')
        calls = message.get('tool_calls') or []
        identities = [call['id'] for call in calls]
        if len(set(identities)) != len(identities):
            raise ValueError('Cannot fit duplicate tool call identities')
        end = start+1+len(identities)
        results = messages[start+1:end]
        if identities and (len(results) != len(identities)
                or any(item.get('role') != 'tool' for item in results)
                or [item.get('tool_call_id') for item in results] != identities):
            raise ValueError('Resolve complete ordered tool batches before fitting context')
        groups.append((start, end))
        start = end
    return groups


def fit_native_messages(
    messages: list[dict[str, Any]], *, count_tokens: Callable[[list[dict[str, Any]]], int],
    token_budget: int, protected_prefix: int, recent_exchanges: int,
    max_field_characters: int, archive_reference: str,
) -> dict[str, Any]:
    """Fit a retrieval-backed view of resolved native exchanges to an exact budget.

    Keep the fixed prefix and prefer the newest complete exchanges. First bound
    old fields, then omit whole old exchanges, then bound an oversized recent
    batch if necessary. No model-authored summary or synthetic evidence is added.
    The caller archives originals and provides both retrieval and token counting.
    The input remains untouched; pending/orphan tool results and oversized fixed
    inputs fail explicitly. All thresholds are supplied by the caller.
    """
    if (type(token_budget) is not int or token_budget <= 0
            or type(recent_exchanges) is not int or recent_exchanges < 0
            or type(protected_prefix) is not int or not 0 <= protected_prefix <= len(messages)
            or type(max_field_characters) is not int or max_field_characters < 0
            or not archive_reference):
        raise ValueError('Invalid conversation fitting parameters')
    groups = _exchange_ranges(messages, protected_prefix)
    original = deepcopy(messages)
    initial_tokens = count_tokens(deepcopy(original))
    if type(initial_tokens) is not int or initial_tokens < 0:
        raise ValueError('Token counter must return a nonnegative integer')
    if initial_tokens <= token_budget:
        return dict(messages=original, prompt_tokens=initial_tokens, original_tokens=initial_tokens,
                    projected=False, replaced_fields=[], omitted_message_ranges=[], archive_reference=archive_reference)
    fixed_tokens = count_tokens(deepcopy(original[:protected_prefix]))
    if type(fixed_tokens) is not int or fixed_tokens < 0:
        raise ValueError('Token counter must return a nonnegative integer')
    if fixed_tokens > token_budget:
        raise ValueError('Fixed inputs exceed the token budget')
    old_count = max(0, len(groups)-recent_exchanges)
    recent_start = groups[old_count][0] if old_count < len(groups) else len(messages)

    def candidate(excerpt: int, drop_groups: int, preserve_recent: bool) -> dict[str, Any] | None:
        projected = project_native_messages(original, protected_prefix=protected_prefix,
            max_field_characters=excerpt, archive_reference=archive_reference,
            protected_suffix=len(original)-recent_start if preserve_recent else 0)
        end = groups[drop_groups-1][1] if drop_groups else protected_prefix
        omitted = [[protected_prefix, end]] if drop_groups else []
        notice = [dict(role='user', content=(
            f'[Earlier resolved exchanges omitted: messages/{protected_prefix}:{end}. '
            f'Full original: {archive_reference}. Retrieve missing evidence before relying on it.]'))] if omitted else []
        view = projected['messages'][:protected_prefix]+notice+projected['messages'][end:]
        count = count_tokens(deepcopy(view))
        if type(count) is not int or count < 0:
            raise ValueError('Token counter must return a nonnegative integer')
        if count > token_budget:
            return None
        replaced = [field for field in projected['replaced_fields']
                    if not protected_prefix <= int(field.split('/')[1]) < end]
        return dict(messages=view, prompt_tokens=count, original_tokens=initial_tokens,
            projected=True, replaced_fields=replaced, omitted_message_ranges=omitted,
            archive_reference=archive_reference)

    excerpt = max_field_characters
    while old_count:
        fitted = candidate(excerpt, 0, True)
        if fitted is not None:
            return fitted
        if excerpt == 0:
            break
        excerpt //= 2
    keep_old = old_count
    while keep_old:
        keep_old //= 2
        fitted = candidate(0, old_count-keep_old, True)
        if fitted is not None:
            return fitted
    excerpt = max_field_characters
    while True:
        fitted = candidate(excerpt, old_count, False)
        if fitted is not None:
            return fitted
        if excerpt == 0:
            raise ValueError('Fixed inputs and native exchange identities exceed the token budget')
        excerpt //= 2


def project_payload_content(
    content: Any,
    *,
    projection: dict[str, Any] | object | None = None,
    metadata: dict[str, Any] | object | None = None,
    fallback_name: str = "item",
    fallback_status: str = "complete",
    max_summary_chars: int = 900,
) -> PayloadProjection:
    """Project retained content into a payload-safe representation."""

    original = str(content or "")
    projection_data = _mapping(projection)
    metadata_data = _mapping(metadata)
    decision = str(projection_data.get("decision", "") or DECISION_KEEP)
    reason = str(projection_data.get("reason", "") or "context_management")
    if decision == DECISION_DROP:
        return PayloadProjection(
            content=format_omitted_content(
                projection=projection_data,
                metadata=metadata_data,
                fallback_name=fallback_name,
                fallback_status=fallback_status,
            ),
            decision=decision,
            replaced=True,
            reason=reason,
        )
    if decision == DECISION_SUMMARIZE:
        return PayloadProjection(
            content=format_summarized_content(
                original,
                projection=projection_data,
                metadata=metadata_data,
                fallback_name=fallback_name,
                fallback_status=fallback_status,
                max_summary_chars=max_summary_chars,
            ),
            decision=decision,
            replaced=True,
            reason=reason,
        )
    return PayloadProjection(content=original, decision=decision or DECISION_KEEP, replaced=False, reason=reason)


def format_omitted_content(
    *,
    projection: dict[str, Any] | object | None = None,
    metadata: dict[str, Any] | object | None = None,
    fallback_name: str = "item",
    fallback_status: str = "complete",
) -> str:
    projection_data = _mapping(projection)
    metadata_data = _mapping(metadata)
    name = _projection_name(projection_data, metadata_data, fallback_name)
    status = _projection_status(projection_data, metadata_data, fallback_status)
    token_count = _projection_token_count(projection_data)
    reason = str(projection_data.get("reason", "") or "context_management")
    token_text = f", {token_count} tokens" if token_count else ""
    return f"[content omitted: {name}, {status}{token_text}, reason: {reason}, available in session log]"


def format_summarized_content(
    content: Any,
    *,
    projection: dict[str, Any] | object | None = None,
    metadata: dict[str, Any] | object | None = None,
    fallback_name: str = "item",
    fallback_status: str = "complete",
    max_summary_chars: int = 900,
) -> str:
    projection_data = _mapping(projection)
    metadata_data = _mapping(metadata)
    name = _projection_name(projection_data, metadata_data, fallback_name)
    status = _projection_status(projection_data, metadata_data, fallback_status)
    token_count = _projection_token_count(projection_data)
    reason = str(projection_data.get("reason", "") or "context_management")
    token_text = f", {token_count} tokens" if token_count else ""
    summary_text = _projection_summary_text(projection_data, metadata_data)
    excerpt = compact_text_excerpt(summary_text or str(content or ""), max_chars=max_summary_chars)
    if not excerpt:
        excerpt = "(empty content)"
    return (
        f"[content summarized: {name}, {status}{token_text}, reason: {reason}, "
        "available in session log]\n"
        f"{excerpt}"
    )


def compact_text_excerpt(content: Any, *, max_chars: int = 900) -> str:
    text = _normalize_text(str(content or ""))
    limit = max(80, int(max_chars))
    if len(text) <= limit:
        return text

    marker = "\n... middle omitted ...\n"
    if limit <= len(marker) + 40:
        return text[:limit].rstrip()

    head_len = max(20, (limit - len(marker)) // 2)
    tail_len = max(20, limit - len(marker) - head_len)
    head = text[:head_len].rstrip()
    tail = text[-tail_len:].lstrip()
    return f"{head}{marker}{tail}"


def _projection_name(projection: dict[str, Any], metadata: dict[str, Any], fallback: str) -> str:
    projection_metadata = _mapping(projection.get("metadata"))
    return str(
        projection.get("name", "")
        or projection_metadata.get("name", "")
        or metadata.get("tool_name", "")
        or metadata.get("name", "")
        or fallback
    )


def _projection_status(projection: dict[str, Any], metadata: dict[str, Any], fallback: str) -> str:
    projection_metadata = _mapping(projection.get("metadata"))
    return str(projection.get("status", "") or projection_metadata.get("status", "") or metadata.get("status", "") or fallback)


def _projection_token_count(projection: dict[str, Any]) -> int:
    projection_metadata = _mapping(projection.get("metadata"))
    return _non_negative_int(projection_metadata.get("linked_token_count") or projection.get("token_count"))


def _projection_summary_text(projection: dict[str, Any], metadata: dict[str, Any]) -> str:
    projection_metadata = _mapping(projection.get("metadata"))
    return str(
        projection.get("summary_text", "")
        or projection_metadata.get("summary_text", "")
        or metadata.get("context_summary_text", "")
        or metadata.get("summary_text", "")
        or ""
    ).strip()


def _normalize_text(content: str) -> str:
    lines = [line.rstrip() for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _non_negative_int(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, resolved)
