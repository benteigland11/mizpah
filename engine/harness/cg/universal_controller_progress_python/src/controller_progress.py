"""A living project document, causal review windows and held corrective input."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class ReviewPolicy:
    update_interval: int
    observation_window: int
    bootstrap_after_turns: int
    maximum_document_characters: int
    maximum_guidance_characters: int
    maximum_request_characters: int
    execution_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        limits = {key: value for key, value in asdict(self).items() if key != 'execution_terms'}
        if any(type(value) is not int or value <= 0 for value in limits.values()):
            raise ValueError('Review limits must be positive integers')
        terms = tuple(self.execution_terms)
        if any(not isinstance(term, str) or not term.strip() for term in terms):
            raise ValueError('execution_terms must be nonempty strings')
        object.__setattr__(self, 'execution_terms', terms)


def execution_term_found(text: str, terms: tuple[str, ...]) -> str | None:
    """Return the first configured execution term appearing as a whole word or phrase."""
    lowered = text.lower()
    for term in terms:
        if re.search(r'(?<![a-z0-9_])'+re.escape(term.lower())+r'(?![a-z0-9_])', lowered):
            return term
    return None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field: '+key)
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError('Non-finite JSON: '+value)


class ControllerProgress:
    """Keep recent observations separate from durable progress and correction.

    This class does no inference, storage, tool execution or worker compaction.
    The caller supplies only completed worker turns in their original order.
    """

    def __init__(self, policy: ReviewPolicy, document: str = '') -> None:
        self.policy = policy
        self.turns = 0
        self.last_review_turn = 0
        self.review_count = 0
        self.recent_turns: list[dict[str, Any]] = []
        self._validate_document(document)
        self.document = document
        self.document_revision = 0
        self.guidance = dict(correction='', evidence='', warrant='')

    def _validate_document(self, value: str) -> None:
        if not isinstance(value, str) or len(value) > self.policy.maximum_document_characters:
            raise ValueError('Invalid or oversized project document')

    def read_document(self, offset: int, maximum_characters: int) -> dict[str, Any]:
        """Read a bounded character range with an explicit continuation offset."""
        if (type(offset) is not int or not 0 <= offset <= len(self.document)
                or type(maximum_characters) is not int or maximum_characters <= 0):
            raise ValueError('Invalid project document range')
        end = min(len(self.document), offset+maximum_characters)
        return dict(text=self.document[offset:end], revision=self.document_revision,
                    offset=offset, end_offset=end, total_characters=len(self.document),
                    next_offset=end if end < len(self.document) else None)

    def edit_document(self, expected_revision: int, old_text: str, new_text: str) -> dict[str, Any]:
        """Apply one exact, unique edit; an empty anchor initializes an empty document.

        Edits preserve the rest of the document, including completed work and its
        rationale. There is no prescribed outline or semantic status schema.
        """
        if type(expected_revision) is not int or expected_revision != self.document_revision:
            raise ValueError('Project document changed; read its current revision before editing')
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise ValueError('Project edits require string anchors and replacement text')
        if not old_text:
            if self.document:
                raise ValueError('An empty edit anchor can only initialize an empty document')
            updated = new_text
        else:
            if self.document.count(old_text) != 1:
                raise ValueError('The edit anchor must occur exactly once in the project document')
            start = self.document.index(old_text)
            return self.edit_range(expected_revision, start, start+len(old_text), new_text)
        return self._commit_document(updated, 0, 0, len(new_text))

    def edit_range(self, expected_revision: int, start_offset: int, end_offset: int,
                   new_text: str) -> dict[str, Any]:
        """Replace a half-open character range in exactly the supplied revision.

        Positions come from read_document; equal positions insert text. Revision
        mismatches, invalid ranges and oversized results never partially apply.
        """
        if type(expected_revision) is not int or expected_revision != self.document_revision:
            raise ValueError('Project document changed; read its current revision before editing')
        if (type(start_offset) is not int or type(end_offset) is not int
                or not 0 <= start_offset <= end_offset <= len(self.document)):
            raise ValueError('Invalid project document character range')
        if not isinstance(new_text, str):
            raise ValueError('Project edits require replacement text')
        updated = self.document[:start_offset]+new_text+self.document[end_offset:]
        return self._commit_document(updated, start_offset, end_offset, start_offset+len(new_text))

    def _commit_document(self, updated: str, start: int, end: int, new_end: int) -> dict[str, Any]:
        self._validate_document(updated)
        if not updated.strip():
            raise ValueError('The project document cannot be erased')
        self.document = updated
        self.document_revision += 1
        return dict(revision=self.document_revision, characters=len(self.document),
                    start_offset=start, previous_end_offset=end, new_end_offset=new_end)

    def observe(self, observation: dict[str, Any]) -> None:
        """Append one completed exchange; old observations age out, state does not."""
        safe = json.loads(json.dumps(observation, allow_nan=False))
        if type(safe.get('turn')) is not int or safe['turn'] != self.turns+1:
            raise ValueError('Observations must be consecutively numbered completed turns')
        self.recent_turns = (self.recent_turns+[safe])[-self.policy.observation_window:]
        self.turns += 1

    def due(self) -> bool:
        """A bootstrap review followed by a fixed completed-turn cadence."""
        return (self.turns >= self.policy.bootstrap_after_turns
                and self.last_review_turn < self.turns
                and (self.turns-self.policy.bootstrap_after_turns) % self.policy.update_interval == 0)

    def envelope(self, reference: str, proposed_input: Any, *, boundary: str,
                 document_characters: int | None = None,
                 observations: list[dict[str, Any]] | None = None) -> str:
        """Build a fresh review from explicit evidence, without hidden history."""
        if not reference.strip() or not boundary.strip():
            raise ValueError('A reference and review boundary are required')
        view = self.recent_turns if observations is None else observations
        if [item.get('turn') for item in view] != [item['turn'] for item in self.recent_turns]:
            raise ValueError('A projected observation view must preserve the completed turn identities and order')
        value = json.dumps(dict(reference=reference, proposed_input=proposed_input,
            boundary=boundary, completed_turns=self.turns, last_review_turn=self.last_review_turn,
            recent_turns=view, project_document=self.read_document(
                0, document_characters if document_characters is not None else max(1, len(self.document))),
            held_guidance=self.guidance), ensure_ascii=False, allow_nan=False)
        if len(value) > self.policy.maximum_request_characters:
            raise ValueError('Controller evidence exceeds its limit; no evidence was truncated')
        return value

    def response_schema(self) -> dict[str, Any]:
        """Transport schema plus stricter local validation in accept()."""
        text = dict(type='string', maxLength=self.policy.maximum_guidance_characters)
        return dict(type='object', additionalProperties=False,
            required=['correction', 'evidence', 'warrant'], properties=dict(
                correction=deepcopy(text), evidence=deepcopy(text), warrant=deepcopy(text)))

    def accept(self, raw: str) -> dict[str, Any]:
        """Atomically validate final guidance after the investigation finishes.

        The string 'None' holds the existing correction. An empty correction
        explicitly clears it. Other text replaces it, including evidence and
        warrant. Thus an aligned observation never implicitly erases guidance.
        """
        if not isinstance(raw, str):
            raise ValueError('Controller output must be JSON text')
        value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
        if not isinstance(value, dict) or set(value) != {'correction', 'evidence', 'warrant'}:
            raise ValueError('Invalid controller decision fields')
        for key in ('correction', 'evidence', 'warrant'):
            if not isinstance(value[key], str) or len(value[key]) > self.policy.maximum_guidance_characters:
                raise ValueError('Invalid or oversized '+key)
        if not self.document.strip():
            raise ValueError('Create the project document before finishing the review')
        correction = value['correction']
        if correction == 'None':
            if value['evidence'] or value['warrant']:
                raise ValueError('Holding guidance requires empty evidence and warrant')
            operation = 'hold'
        else:
            if (correction and not correction.strip()) or not value['evidence'].strip() or not value['warrant'].strip():
                raise ValueError('Changing guidance requires substantive evidence and warrant')
            operation = 'replace' if correction else 'clear'
            term = execution_term_found(correction, self.policy.execution_terms)
            if operation == 'replace' and term is not None:
                # Guidance corrects the destination, never the technique. The harness
                # already reports tool and size problems to the worker directly.
                raise ValueError(f'Correction names execution technique ({term!r}). State the unmet reference outcome '
                                 'or violated constraint and the evidence; leave tools, commands, chunking and size '
                                 'limits to the worker and the harness. Hold with "None" if no reference mismatch exists.')
        if self.last_review_turn == self.turns and self.review_count:
            raise ValueError('This completed-turn boundary was already reviewed')
        if operation == 'replace':
            self.guidance = {key:value[key] for key in ('correction', 'evidence', 'warrant')}
        elif operation == 'clear':
            self.guidance = dict(correction='', evidence='', warrant='')
        self.last_review_turn = self.turns
        self.review_count += 1
        return deepcopy(value) | dict(operation=operation)

    def applied_guidance(self) -> str:
        """Render one held instruction; the caller controls its message placement."""
        if not self.guidance['correction']:
            return ''
        return '\n\n'.join(self.guidance[key] for key in ('correction', 'evidence', 'warrant'))

    def export_state(self) -> dict[str, Any]:
        return deepcopy(dict(schema=2, policy=asdict(self.policy), turns=self.turns,
            last_review_turn=self.last_review_turn, review_count=self.review_count,
            recent_turns=self.recent_turns, document=self.document,
            document_revision=self.document_revision, guidance=self.guidance))

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> ControllerProgress:
        if state.get('schema') != 2:
            raise ValueError('Legacy controller state requires its original harness; start a new document-based session')
        result = cls(ReviewPolicy(**state['policy']), state['document'])
        for key in ('turns', 'last_review_turn', 'review_count', 'document_revision'):
            if type(state[key]) is not int or state[key] < 0:
                raise ValueError('Invalid persisted review counters')
            setattr(result, key, state[key])
        if result.last_review_turn > result.turns:
            raise ValueError('Review cannot precede its observations')
        recent = state['recent_turns']
        expected = list(range(max(1, result.turns-result.policy.observation_window+1), result.turns+1))
        if not isinstance(recent, list) or [item.get('turn') for item in recent] != expected:
            raise ValueError('Persisted observation window is incomplete or unordered')
        value = state['guidance']
        if (not isinstance(value, dict) or set(value) != set(result.guidance)
                or any(not isinstance(item, str) or len(item) > result.policy.maximum_guidance_characters
                       for item in value.values())):
            raise ValueError('Invalid persisted guidance')
        result.guidance = deepcopy(value)
        result.recent_turns = deepcopy(recent)
        return result
