"""Bounded, channel-isolated detection of sustained repetitive generated text."""
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
import hashlib
import json
import zlib


@dataclass(frozen=True)
class Repetition:
    channel: str
    observed_chars: int
    window_chars: int
    compression_ratio: float | None
    repeated_fraction: float | None
    kind: str = 'text_window'
    repeated_calls: int = 0


class RepetitionGuard:
    """Detect highly compressible windows dominated by repeated substantial blocks.

    This is a heuristic, not a semantic classifier. Repetitive legitimate data can
    trigger it. Callers own cancellation and must never report a detected stream
    as a successful completion. Use one instance per response.
    """

    def __init__(self, window_chars: int = 8192, check_every: int = 512,
                 block_chars: int = 64, min_occurrences: int = 4,
                 repeated_fraction: float = .85, compression_ratio: float = .12,
                 max_channels: int = 64, identical_tool_calls: int | None = None,
                 maximum_tool_characters: int = 262144) -> None:
        if not (window_chars >= block_chars > 0 and check_every > 0 and
                min_occurrences >= 2 and max_channels > 0 and
                0 < repeated_fraction <= 1 and 0 < compression_ratio < 1):
            raise ValueError('Invalid repetition policy')
        if (identical_tool_calls is not None and (type(identical_tool_calls) is not int or identical_tool_calls < 2)
                or type(maximum_tool_characters) is not int or maximum_tool_characters <= 0):
            raise ValueError('Invalid tool repetition policy')
        self.window_chars = window_chars
        self.check_every = check_every
        self.block_chars = block_chars
        self.min_occurrences = min_occurrences
        self.repeated_fraction = repeated_fraction
        self.compression_ratio = compression_ratio
        self.max_channels = max_channels
        self.identical_tool_calls = identical_tool_calls
        self.maximum_tool_characters = maximum_tool_characters
        self._states: dict[str, tuple[str, int, int]] = {}
        self._tools: OrderedDict[tuple[int, int], dict] = OrderedDict()
        self._completed: dict[int, deque] = {}

    def policy(self) -> dict:
        """Return every resolved setting for reproducible request auditing."""
        return {key: getattr(self, key) for key in (
            'window_chars', 'check_every', 'block_chars', 'min_occurrences',
            'repeated_fraction', 'compression_ratio', 'max_channels',
            'identical_tool_calls', 'maximum_tool_characters')}

    def _tool(self, choice: int, tool: dict) -> Repetition | None:
        """Count identical complete calls across indices, never network chunks."""
        if self.identical_tool_calls is None:
            return None
        key = (choice, tool.get('index', 0))
        state = self._tools.setdefault(key, dict(name='', arguments='', recorded=False))
        self._tools.move_to_end(key)
        if len(self._tools) > self.max_channels:
            self._tools.popitem(last=False)
        function = tool.get('function', {})
        state['name'] += function.get('name', '')
        state['arguments'] += function.get('arguments', '')
        if len(state['name']) + len(state['arguments']) > self.maximum_tool_characters:
            raise ValueError('Tool output exceeded the configured detection bound')
        if not state['name'] or state['recorded']:
            return None
        try:
            arguments = json.loads(state['arguments'])
        except ValueError:
            return None
        if not isinstance(arguments, dict):
            return None
        state['recorded'] = True
        signature = hashlib.sha256(json.dumps([state['name'], arguments], sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if choice not in self._completed and len(self._completed) >= self.max_channels:
            raise ValueError('Too many output choices')
        history = self._completed.setdefault(choice, deque(maxlen=self.identical_tool_calls))
        history.append((key, signature))
        if (len(history) == self.identical_tool_calls and len({k for k, _ in history}) == len(history)
                and len({s for _, s in history}) == 1):
            return Repetition(f'{choice}:tool_calls', 0, 0, None, None,
                              'identical_tool_calls', len(history))
        return None

    def feed(self, channel: str, text: str) -> Repetition | None:
        """Consume arbitrary chunk boundaries; stop at first detected window."""
        if channel not in self._states and len(self._states) >= self.max_channels:
            raise ValueError('Too many output channels')
        tail, total, since = self._states.get(channel, ('', 0, 0))
        # Fixed observation boundaries make detection independent of SSE chunking.
        while text:
            take = min(len(text), self.check_every - since)
            tail = (tail + text[:take])[-self.window_chars:]
            text = text[take:]
            total += take
            since += take
            if since < self.check_every:
                continue
            since = 0
            if len(tail) < self.window_chars:
                continue
            raw = tail.encode('utf8')
            ratio = len(zlib.compress(raw)) / len(raw)
            if ratio > self.compression_ratio:
                continue
            blocks = [tail[i:i + self.block_chars]
                      for i in range(len(tail) - self.block_chars + 1)]
            counts = Counter(blocks)
            fraction = sum(counts[b] >= self.min_occurrences for b in blocks) / len(blocks)
            if fraction >= self.repeated_fraction:
                self._states[channel] = (tail, total, since)
                return Repetition(channel, total, len(tail), ratio, fraction)
        self._states[channel] = (tail, total, since)
        return None

    def observe(self, event: dict) -> Repetition | None:
        """Inspect OpenAI chat chunks, including reasoning and tool JSON deltas."""
        for choice in event.get('choices', []):
            index = choice.get('index', 0)
            delta = choice.get('delta') or choice.get('message') or {}
            for key in ('content', 'reasoning_content', 'reasoning'):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    result = self.feed(f'{index}:{key}', value)
                    if result:
                        return result
            for tool in delta.get('tool_calls', []):
                repeated_call = self._tool(index, tool)
                if repeated_call:
                    return repeated_call
                value = tool.get('function', {}).get('arguments', '')
                if value:
                    result = self.feed(f'{index}:tool:{tool.get("index", 0)}', value)
                    if result:
                        return result
        return None
