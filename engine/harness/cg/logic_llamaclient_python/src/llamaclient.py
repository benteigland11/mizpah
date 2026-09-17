"""
Llama.cpp server API client for chat completions.
Works with llama-server's OpenAI-compatible API.
"""

import asyncio
import aiohttp
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

TOOL_CALL_PATTERN = re.compile(r'<tool_call>\s*(\{[\s\S]*?\})\s*</tool_call>')


def extract_xml_tool_calls(text: str) -> tuple:
    """Extract tool calls from <tool_call> blocks, handling closed and unclosed tags.

    Returns:
        (tool_calls: list[dict], clean_text: str)
    """
    strict_matches = TOOL_CALL_PATTERN.findall(text)
    if strict_matches:
        calls = []
        for match in strict_matches:
            try:
                calls.append(json.loads(match))
            except json.JSONDecodeError:
                pass
        clean = re.sub(r'<tool_call>\s*\{[\s\S]*?\}\s*</tool_call>', '', text).strip()
        return calls, clean

    if '<tool_call>' not in text:
        return [], text

    parts = re.split(r'</?tool_call>', text)
    calls = []
    text_parts = []
    for part in parts:
        stripped = part.strip()
        if stripped.startswith('{'):
            try:
                calls.append(json.loads(stripped))
                continue
            except json.JSONDecodeError:
                pass
            brace_count = 0
            end_idx = -1
            for i, ch in enumerate(stripped):
                if ch == '{':
                    brace_count += 1
                elif ch == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            if end_idx > 0:
                try:
                    calls.append(json.loads(stripped[:end_idx]))
                    remainder = stripped[end_idx:].strip()
                    if remainder:
                        text_parts.append(remainder)
                    continue
                except json.JSONDecodeError:
                    pass
        else:
            match = re.match(r'(\w+)\s*(\{.*)', stripped, re.DOTALL)
            if match:
                func_name = match.group(1)
                json_part = match.group(2).strip()
                try:
                    args = json.loads(json_part)
                    calls.append({"name": func_name, "arguments": args})
                    continue
                except json.JSONDecodeError:
                    pass
        text_parts.append(part)

    return calls, ''.join(text_parts).strip()


def get_llama_base_url(base_url: Optional[str] = None) -> str:
    """Get llama-server base URL.

    Priority: explicit param > LLAMA_BASE_URL env > LLAMA_SERVER_URL env > default.
    """
    if base_url:
        return base_url.rstrip("/")
    env_url = os.getenv("LLAMA_BASE_URL") or os.getenv("LLAMA_SERVER_URL")
    if env_url:
        return env_url.rstrip("/")
    return "http://localhost:8080/v1"


def get_llama_model(model: Optional[str] = None) -> str:
    """Get llama model name.

    Priority: explicit param > LLAMA_MODEL env > default "model".
    """
    if model:
        return model
    return os.getenv("LLAMA_MODEL", "model")


if __package__:
    from .native import IncompleteGeneration, KnownIssues, NativeAssembler, NativeProtocol, SyncNativeTransport
else:
    from native import IncompleteGeneration, KnownIssues, NativeAssembler, NativeProtocol, SyncNativeTransport


class LlamaClient(NativeProtocol):
    """Client for llama-server's OpenAI-compatible API.

    Supports chat, streaming, tool calling (native/JSON/XML modes),
    vision, JSON mode, GBNF grammars, token counting, and health checks.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = 120,
        logger: Optional[Any] = None,
        known_issues: Optional[Union[KnownIssues, Dict[str, Any]]] = None,
        maximum_response_bytes: int = 16000000,
        diagnostic_characters: int = 8192,
    ) -> None:
        if timeout <= 0 or maximum_response_bytes <= 0 or diagnostic_characters <= 0:
            raise ValueError('Transport limits must be positive')
        self.known_issues = known_issues if isinstance(known_issues, KnownIssues) else KnownIssues(**(known_issues or {}))
        self.maximum_response_bytes = maximum_response_bytes
        self.diagnostic_characters = diagnostic_characters
        self.base_url = get_llama_base_url(base_url)
        self.timeout = timeout
        self.default_model = get_llama_model(default_model)
        self.logger = logger if logger is not None else logging.getLogger(__name__)
        self._active_session: Optional[aiohttp.ClientSession] = None
        self._active_response: Optional[aiohttp.ClientResponse] = None

    async def abort(self) -> None:
        """Abort any in-flight stream_chat request.

        Closes the active HTTP response directly, which propagates through
        aiohttp's connection layer to close the underlying TCP transport.
        llama-server detects the disconnection and stops inference, freeing
        the slot for the next request.

        Uses response.close() (sync) rather than session.close() because
        session.close() may drain gracefully without force-closing the socket.
        response.close() immediately calls connection.close() → transport.close(),
        which is what sends the TCP FIN/RST that llama-server detects.

        Safe to call when no stream is active — it is a no-op in that case.
        Safe to call multiple times.
        """
        response = self._active_response
        if response is not None:
            response.close()  # sync: closes connection → transport immediately

    async def health_check(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Check if llama-server is reachable."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.base_url}/models",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 200:
                        return {"available": True, "base_url": self.base_url,
                                "status_code": response.status}
                    return {"available": False, "base_url": self.base_url,
                            "status_code": response.status,
                            "error": f"Server returned status {response.status}"}
            except aiohttp.ClientConnectorError:
                return {"available": False, "base_url": self.base_url,
                        "error": f"Cannot connect to llama-server at {self.base_url}"}
            except asyncio.TimeoutError:
                return {"available": False, "base_url": self.base_url,
                        "error": f"Timeout connecting to {self.base_url}"}
            except Exception as e:
                return {"available": False, "base_url": self.base_url,
                        "error": f"Health check failed: {e}"}

    async def verify_connection(self, raise_on_error: bool = True) -> bool:
        """Verify server is available."""
        health = await self.health_check()
        if not health["available"]:
            if raise_on_error:
                raise ConnectionError(
                    f"llama-server is not available at {self.base_url}. "
                    f"Error: {health.get('error', 'Unknown error')}. "
                    f"Start with: llama-server -m /path/to/model.gguf --port <port>"
                )
            return False
        return True

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        stop: Optional[List[str]] = None,
        seed: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        grammar: Optional[str] = None,
        min_p: Optional[float] = None,
        repeat_penalty: Optional[float] = None,
        repeat_last_n: Optional[int] = None,
        mirostat: Optional[int] = None,
        mirostat_tau: Optional[float] = None,
        mirostat_eta: Optional[float] = None,
        typical_p: Optional[float] = None,
        tfs_z: Optional[float] = None,
        parallel_tool_calls: Optional[bool] = None,
        tool_call_format: Optional[str] = None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Send messages and receive a response.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            model: Override default model.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Max response tokens.
            top_p: Nucleus sampling.
            top_k: Top-k sampling.
            frequency_penalty: Repetition reduction (-2.0-2.0).
            presence_penalty: New-topic encouragement (-2.0-2.0).
            stop: Stop sequences.
            seed: Deterministic sampling seed.
            tools: Tool definitions for native tool calling.
            tool_choice: "auto"/"required"/"none" or specific tool dict.
            response_format: {"type": "json_object"} for JSON mode.
            grammar: GBNF grammar string.
            min_p: Min probability threshold (llama.cpp).
            repeat_penalty: Repetition penalty (llama.cpp).
            repeat_last_n: Tokens to consider for repeat penalty.
            mirostat: Mirostat mode (0=off, 1, 2).
            mirostat_tau: Mirostat target entropy.
            mirostat_eta: Mirostat learning rate.
            typical_p: Locally typical sampling.
            tfs_z: Tail-free sampling.
            parallel_tool_calls: Allow multiple tool calls per turn.
            tool_call_format: None=native, "json", or "xml".
            verbose: Print request summary to stdout.

        Returns:
            Dict: content, usage, model, tool_calls, finish_reason.
        """
        effective_format = tool_call_format
        effective_messages = messages

        if effective_format in ("json", "xml"):
            effective_messages = self._sanitize_messages_manual(messages, effective_format)
            if tools:
                self._inject_system_prompt(
                    effective_messages,
                    self._build_tool_prompt(tools, effective_format),
                )

        payload = self._build_payload(
            model=model or self.default_model,
            messages=effective_messages,
            temperature=temperature, max_tokens=max_tokens, top_p=top_p,
            top_k=top_k, frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty, stop=stop, seed=seed,
            min_p=min_p, repeat_penalty=repeat_penalty, repeat_last_n=repeat_last_n,
            mirostat=mirostat, mirostat_tau=mirostat_tau, mirostat_eta=mirostat_eta,
            typical_p=typical_p, tfs_z=tfs_z,
        )
        if effective_format is None:
            if tools is not None:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if parallel_tool_calls is not None:
                payload["parallel_tool_calls"] = parallel_tool_calls
        if response_format is not None:
            payload["response_format"] = response_format
        elif effective_format == "json":
            payload["response_format"] = {"type": "json_object"}
        if grammar:
            payload["grammar"] = grammar

        if verbose:
            self._log_request(payload, effective_format)

        try:
            if self.known_issues.repetition is not None:
                data = await self.chat_raw(payload)
                if any(choice['finish_reason'] not in ('stop', 'tool_calls') for choice in data['choices']):
                    raise IncompleteGeneration('incomplete_generation',
                        'Generation did not finish cleanly; no tool calls are executable',
                        status=200, definitive=True,
                        evidence=dict(finish_reasons=[choice['finish_reason'] for choice in data['choices']]))
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.base_url}/chat/completions", json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()

            result = self._empty_result(model or self.default_model)
            result["model"] = data.get("model", result["model"])

            if data.get("choices"):
                choice = data["choices"][0]
                message = choice.get("message", {})
                raw_content = message.get("content") or ""
                result["content"] = raw_content
                result["finish_reason"] = choice.get("finish_reason")

                found = False
                if not found and effective_format == "xml" and raw_content:
                    found = self._apply_xml_calls(result, raw_content)
                if not found and message.get("tool_calls"):
                    result["tool_calls"] = self._fix_xml_native_calls(
                        message["tool_calls"], effective_format
                    )
                    found = True
                if not found and effective_format == "json" and raw_content:
                    self._apply_json_calls(result, raw_content)

            if "usage" in data:
                result["usage"] = data["usage"]

            return result

        except aiohttp.ClientError as error:
            raise RuntimeError(f"Failed to connect to llama-server at {self.base_url}: {error}") from error

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        frequency_penalty: Optional[float] = None,
        presence_penalty: Optional[float] = None,
        stop: Optional[List[str]] = None,
        seed: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        response_format: Optional[Dict[str, Any]] = None,
        grammar: Optional[str] = None,
        min_p: Optional[float] = None,
        repeat_penalty: Optional[float] = None,
        repeat_last_n: Optional[int] = None,
        parallel_tool_calls: Optional[bool] = None,
        tool_call_format: Optional[str] = None,
    ):
        """Stream chat response token by token.

        Yields dicts:
            {"type": "reasoning", "content": str}
            {"type": "content", "content": str}
            {"type": "tool_calls", "tool_calls": list, "content": str, ...}
            {"type": "usage", "usage": dict, "done": True}
        """
        effective_format = tool_call_format
        effective_messages = messages

        if effective_format in ("json", "xml"):
            effective_messages = self._sanitize_messages_manual(messages, effective_format)
            if tools:
                self._inject_system_prompt(
                    effective_messages,
                    self._build_tool_prompt(tools, effective_format),
                )

        payload = self._build_payload(
            model=model or self.default_model,
            messages=effective_messages,
            temperature=temperature, max_tokens=max_tokens, top_p=top_p,
            top_k=top_k, frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty, stop=stop, seed=seed,
            min_p=min_p, repeat_penalty=repeat_penalty, repeat_last_n=repeat_last_n,
        )
        payload["stream"] = True

        if effective_format is None:
            if tools is not None:
                payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            if parallel_tool_calls is not None:
                payload["parallel_tool_calls"] = parallel_tool_calls
        if response_format is not None:
            payload["response_format"] = response_format
        elif effective_format == "json":
            payload["response_format"] = {"type": "json_object"}
        if grammar:
            payload["grammar"] = grammar

        assembled = NativeAssembler()
        async for event in self.stream_raw(payload):
            assembled.add(event)
            for choice in event.get("choices", []):
                delta = choice.get("delta", {})
                index = choice.get("index", 0)
                for field in ("reasoning_content", "reasoning"):
                    if delta.get(field):
                        yield {"type": "reasoning", "content": delta[field], "index": index}
                if delta.get("content"):
                    yield {"type": "content", "content": delta["content"], "index": index}
                if delta.get("tool_calls"):
                    yield {"type": "tool_call_delta", "tool_calls": delta["tool_calls"],
                           "index": index, "provisional": True}
        complete = assembled.finish()
        if any(choice['finish_reason'] not in ('stop', 'tool_calls') for choice in complete['choices']):
            raise IncompleteGeneration('incomplete_generation',
                'Generation did not finish cleanly; no tool calls are executable', status=200,
                definitive=True, evidence=dict(finish_reasons=[choice['finish_reason'] for choice in complete['choices']]))
        for choice in complete["choices"]:
            message = choice["message"]
            if message.get("tool_calls"):
                yield {"type": "tool_calls", "tool_calls": message["tool_calls"],
                       "content": message.get("content"), "index": choice["index"],
                       "finish_reason": choice["finish_reason"]}
            elif effective_format and message.get("content"):
                chunk = self._parse_tool_calls_from_content(message["content"], effective_format)
                if chunk:
                    yield chunk
        yield {"type": "usage", "usage": complete.get("usage"), "done": True,
               "finish_reasons": [choice["finish_reason"] for choice in complete["choices"]]}

    async def get_models(self) -> List[Dict[str, Any]]:
        """Return list of available models from the server."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{self.base_url}/models",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get("data", [])
            except Exception as e:
                self.logger.error(f"[LLAMA_CLIENT] Failed to get models: {e}")
                return []

    async def get_server_properties(self) -> Dict[str, Any]:
        """Return server configuration properties (e.g. n_ctx)."""
        root_url = self.base_url.replace("/v1", "")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{root_url}/props",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return {}
            except Exception as e:
                self.logger.warning(f"[LLAMA_CLIENT] Failed to get server properties: {e}")
                return {}

    async def count_tokens(self, text: str) -> int:
        """Count tokens in text using the server's tokenizer."""
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url.replace('/v1', '')}/tokenize",
                    json={"content": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return len(data.get("tokens", []))
            except aiohttp.ClientError as e:
                raise RuntimeError(f"Failed to count tokens at {self.base_url}: {e}") from e

    async def complete(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        stop: Optional[List[str]] = None,
        seed: Optional[int] = None,
        min_p: Optional[float] = None,
        repeat_penalty: Optional[float] = None,
        repeat_last_n: Optional[int] = None,
        grammar: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a completion for a raw prompt (non-chat format)."""
        payload: Dict[str, Any] = {"model": model or self.default_model, "prompt": prompt}
        for key, val in [
            ("temperature", temperature), ("max_tokens", max_tokens), ("top_p", top_p),
            ("top_k", top_k), ("stop", stop), ("seed", seed), ("min_p", min_p),
            ("repeat_penalty", repeat_penalty), ("repeat_last_n", repeat_last_n),
        ]:
            if val is not None:
                payload[key] = val
        if grammar:
            payload["grammar"] = grammar

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    f"{self.base_url}/completions",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    result = self._empty_result(model or self.default_model)
                    result["model"] = data.get("model", result["model"])
                    if data.get("choices"):
                        result["content"] = data["choices"][0].get("text", "")
                    if "usage" in data:
                        result["usage"] = data["usage"]
                    return result
            except aiohttp.ClientError as e:
                raise RuntimeError(f"Completion failed at {self.base_url}: {e}") from e

    @staticmethod
    def encode_image_base64(image_path: Union[str, Path]) -> str:
        """Encode an image file to base64 string."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {image_path}")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    @staticmethod
    def create_image_message(
        role: str,
        text: str,
        image_url: Optional[str] = None,
        image_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Create a multi-modal message dict for vision models.

        Provide exactly one of image_url or image_path.
        """
        if (image_url is None) == (image_path is None):
            raise ValueError("Provide exactly one of image_url or image_path")
        content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        if image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        else:
            b64 = LlamaClient.encode_image_base64(image_path)  # type: ignore[arg-type]
            ext = Path(image_path).suffix.lower().lstrip(".")  # type: ignore[arg-type]
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
            content.append({"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}})
        return {"role": role, "content": content}

    # --- Internal helpers ---

    def _empty_result(self, model: str) -> Dict[str, Any]:
        return {
            "content": "",
            "usage": None,
            "model": model,
            "finish_reason": None,
            "tool_calls": None,
        }

    def _build_payload(self, model: str, messages: List[Dict[str, Any]], **kw: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"model": model, "messages": messages}
        for key in (
            "temperature", "max_tokens", "top_p", "top_k", "frequency_penalty",
            "presence_penalty", "stop", "seed", "min_p", "repeat_penalty",
            "repeat_last_n", "mirostat", "mirostat_tau", "mirostat_eta",
            "typical_p", "tfs_z",
        ):
            if kw.get(key) is not None:
                payload[key] = kw[key]
        return payload

    def _sanitize_messages_manual(
        self, messages: List[Dict[str, Any]], fmt: str
    ) -> List[Dict[str, Any]]:
        result = []
        for msg in messages:
            m = msg.copy()
            if m["role"] == "tool":
                m["role"] = "user"
                m["content"] = f"TOOL_RESULT ({m.get('name', 'tool')}): {m.get('content', '')}"
                m.pop("tool_call_id", None)
                m.pop("name", None)
            elif m["role"] == "assistant" and "tool_calls" in m:
                if fmt == "xml":
                    blocks = []
                    for tc in m["tool_calls"]:
                        func = tc.get("function", {})
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        blocks.append(
                            f'<tool_call>\n{{"name": "{func.get("name", "")}", '
                            f'"arguments": {json.dumps(args)}}}\n</tool_call>'
                        )
                    existing = m.get("content") or ""
                    m["content"] = (existing + "\n" + "\n".join(blocks)).strip() if existing else "\n".join(blocks)
                else:
                    if not m.get("content"):
                        m["content"] = f"THOUGHT: I need to call some tools.\nCALLS: {json.dumps(m['tool_calls'])}"
                m.pop("tool_calls", None)
            result.append(m)
        return result

    def _inject_system_prompt(self, messages: List[Dict[str, Any]], prompt: str) -> None:
        for i, msg in enumerate(messages):
            if msg["role"] == "system":
                messages[i] = msg.copy()
                messages[i]["content"] += "\n\n" + prompt
                return
        messages.insert(0, {"role": "system", "content": prompt})

    def _build_tool_prompt(self, tools: List[Dict[str, Any]], fmt: str) -> str:
        if fmt == "xml":
            return (
                "\n## TOOL USE INSTRUCTIONS\nYou have access to the following tools:\n\n"
                f"<tools>\n{json.dumps(tools, indent=2)}\n</tools>\n\n"
                "To call a tool, write a <tool_call> block:\n"
                '<tool_call>\n{"name": "tool_name", "arguments": {"arg1": "value1"}}\n</tool_call>\n\n'
                "If no tools are needed, respond normally.\n"
            )
        return (
            "\n## TOOL USE INSTRUCTIONS\nYou MUST output a valid JSON object.\n\n"
            f"### Available Tools\n{json.dumps(tools, indent=2)}\n\n"
            '### Output Format\n{"content": "notes", "tool_calls": [{"name": "tool_name", "arguments": {"arg1": "value1"}}]}\n'
            'If no tools needed: {"content": "Your response", "tool_calls": []}\n'
        )

    def _apply_xml_calls(self, result: Dict[str, Any], raw: str) -> bool:
        import uuid
        calls, clean = extract_xml_tool_calls(raw)
        if not calls:
            return False
        result["tool_calls"] = [
            {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
             "function": {"name": c.get("name"), "arguments": json.dumps(c.get("arguments", {}))}}
            for c in calls
        ]
        result["content"] = clean
        return True

    def _apply_json_calls(self, result: Dict[str, Any], raw: str) -> None:
        import uuid
        json_data = None
        try:
            json_data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                key_idx = raw.find('"tool_calls"')
                if key_idx != -1:
                    start = raw.rfind('{', 0, key_idx)
                    if start != -1:
                        brace = 0
                        for i in range(start, len(raw)):
                            if raw[i] == '{':
                                brace += 1
                            elif raw[i] == '}':
                                brace -= 1
                                if brace == 0:
                                    json_data = json.loads(raw[start:i + 1])
                                    break
            except Exception:
                pass
        if json_data:
            result["content"] = json_data.get("thought") or json_data.get("content") or ""
            raw_calls = json_data.get("tool_calls", [])
            if raw_calls:
                result["tool_calls"] = [
                    {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                     "function": {"name": c.get("name"),
                                  "arguments": json.dumps(c.get("arguments", {}))}}
                    for c in raw_calls
                ]

    def _fix_xml_native_calls(
        self, native_calls: List[Dict[str, Any]], effective_format: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Fix server-garbled tool calls in XML mode where name field contains JSON."""
        import uuid
        if effective_format != "xml":
            return native_calls
        fixed = []
        for tc in native_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if name.startswith("{"):
                extracted, _ = extract_xml_tool_calls(f"<tool_call>\n{name}\n</tool_call>")
                if not extracted:
                    try:
                        extracted = [json.loads(name)]
                    except json.JSONDecodeError:
                        extracted = []
                for call_data in extracted:
                    fixed.append({
                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": call_data.get("name", ""),
                            "arguments": json.dumps(call_data.get("arguments", {})),
                        },
                    })
            else:
                fixed.append(tc)
        return fixed

    def _parse_tool_calls_from_content(
        self,
        raw_content: str,
        effective_format: str,
    ) -> Optional[Dict[str, Any]]:
        """Parse tool calls from buffered stream content. Returns chunk dict or None."""
        import uuid

        if effective_format == "xml":
            calls, clean = extract_xml_tool_calls(raw_content)
            if calls:
                return {
                    "type": "tool_calls",
                    "tool_calls": [
                        {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                         "function": {"name": c.get("name"),
                                      "arguments": json.dumps(c.get("arguments", {}))}}
                        for c in calls
                    ],
                    "content": clean,
                }

        elif effective_format == "json":
            json_data = None
            try:
                json_data = json.loads(raw_content)
            except json.JSONDecodeError:
                try:
                    key_idx = raw_content.find('"tool_calls"')
                    if key_idx != -1:
                        start = raw_content.rfind('{', 0, key_idx)
                        if start != -1:
                            brace = 0
                            for i in range(start, len(raw_content)):
                                if raw_content[i] == '{':
                                    brace += 1
                                elif raw_content[i] == '}':
                                    brace -= 1
                                    if brace == 0:
                                        json_data = json.loads(raw_content[start:i + 1])
                                        break
                except Exception:
                    pass
            if json_data:
                raw_calls = json_data.get("tool_calls", [])
                if raw_calls:
                    return {
                        "type": "tool_calls",
                        "tool_calls": [
                            {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                             "function": {"name": c.get("name"),
                                          "arguments": json.dumps(c.get("arguments", {}))}}
                            for c in raw_calls
                        ],
                        "content": json_data.get("thought") or json_data.get("content") or "",
                    }

        return None

    def _log_request(self, payload: Dict[str, Any], fmt: Optional[str]) -> None:
        self.logger.debug(
            f"[LLAMA_CLIENT] model={payload.get('model')} "
            f"messages={len(payload.get('messages', []))} "
            f"tools={len(payload.get('tools', []))} "
            f"format={fmt or 'native'}"
        )
