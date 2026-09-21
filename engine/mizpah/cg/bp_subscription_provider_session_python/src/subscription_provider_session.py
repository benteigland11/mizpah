"""A signed-in LLM provider as one object: login, status, logout, transport.

The composition: a ``ProviderProfile`` says which sign-in flow and wire
dialect a provider uses; the OAuth leaves run the flow; the credential store
keeps the result owner-only with quarantine; the refresh policy keeps it
fresh; the codec lets a Chat-Completions caller talk to a Responses-only
endpoint; the estimator stands in for a tokenizer the provider does not
expose. ``transport()`` returns the one callable a ``ModelClient`` takes.

HTTP is injected through ``HttpCall`` so tests and examples never touch the
network; ``urllib_http`` is the stdlib default for real use.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from cg.backend_chat_messages_codec_python.src.chat_messages_codec import (
    chat_to_messages,
    fold_sse as fold_messages_sse,
    messages_to_chat,
)
from cg.backend_chat_responses_codec_python.src.chat_responses_codec import (
    chat_to_responses,
    fold_sse,
    responses_to_chat,
)
from cg.backend_hosted_token_estimator_python.src.hosted_token_estimator import HostedTokenEstimator
from cg.backend_llm_provider_profiles_python.src.llm_provider_profiles import (
    ApiKeyAuth,
    AuthorizedUserFileAuth,
    DeviceCodeFlow,
    NoAuth,
    OAuthPkceFlow,
    ProviderProfile,
)
from cg.infra_local_http_server_python.src.single_request_capture import SingleRequestCapture
from cg.security_credential_store_python.src.credential_store import (
    CredentialRecord,
    CredentialStore,
    QuarantinedCredential,
)
from cg.security_jwt_claims_unverified_python.src.jwt_claims_unverified import (
    MalformedJwt,
    namespaced_claim,
    unverified_claims,
)
from cg.universal_bearer_refresh_policy_python.src.bearer_refresh_policy import (
    BearerSession,
    RefreshFailed,
    RefreshPolicy,
    classify_refresh_failure,
)
from cg.universal_oauth_device_code_python.src.oauth_device_code import (
    DeviceCodeConfig,
    DeviceFlowError,
    RequestDescriptor,
    run_device_flow,
)
from cg.universal_oauth_pkce_python.src.oauth_pkce import (
    OAuthPkceConfig,
    build_authorization_url,
    build_refresh_token_request,
    build_token_exchange_request,
    create_pkce_authorization,
    parse_oauth_callback,
)
from cg.universal_oauth_token_response_python.src.oauth_token_response import (
    OAuthTokenRecord,
    normalize_oauth_token_response,
)
from cg.universal_open_in_browser_python.src.open_in_browser import open_url

__all__ = [
    # the feature
    "ProviderSession", "ProviderTransport", "LoginPrompt", "LoginError", "NotSignedIn",
    "HttpResponse", "HttpCall", "WireResponse", "urllib_http",
    # the inputs a consumer builds (re-exported so nothing reaches past the façade)
    "ProviderProfile", "OAuthPkceFlow", "DeviceCodeFlow", "ApiKeyAuth", "NoAuth", "AuthorizedUserFileAuth", "ModelInfo",
    "CredentialStore", "CredentialRecord", "QuarantinedCredential", "RefreshPolicy", "RefreshFailed", "HostedTokenEstimator",
]

DEFAULT_LOGIN_TIMEOUT_SECONDS = 300.0
DEFAULT_MAXIMUM_RESPONSE_BYTES = 16_000_000
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


HttpCall = Callable[[str, str, Mapping[str, str], bytes | None, float], HttpResponse]
"""``http(method, url, headers, body, timeout_seconds) -> HttpResponse``."""


def urllib_http(maximum_response_bytes: int = DEFAULT_MAXIMUM_RESPONSE_BYTES) -> HttpCall:
    """The stdlib ``HttpCall``: single attempt, bounded read, non-2xx returned not raised."""

    def call(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        request = Request(url, body, dict(headers), method=method)
        try:
            response = urlopen(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            # Read in chunks and say so: a streamed reply arriving is a live model; silence on an open socket
            # is a stalled one, and the two looked the same from outside for fifteen minutes.
            progress = getattr(call, "progress", None)
            chunks: list[bytes] = []
            total = 0
            last_event = ""
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum_response_bytes:
                    raise OSError("response exceeded the configured byte limit")
                if progress is not None:
                    i = chunk.rfind(b"event: ")
                    if i >= 0:
                        last_event = chunk[i + 7:chunk.find(b"\n", i) if chunk.find(b"\n", i) > 0 else None].decode("utf-8", "replace").strip()
                    try:
                        progress(total, last_event)
                    except Exception:  # noqa: BLE001 — progress is a courtesy, never the call
                        pass
            raw = b"".join(chunks)
            return HttpResponse(response.status, {k.lower(): v for k, v in response.headers.items()}, raw)

    return call


@dataclass(frozen=True)
class WireResponse:
    """Same shape as a model client's wire response: status, body, timing, error."""

    status: int | None
    body: str
    elapsed_seconds: float
    error: str | None = None
    failure_kind: str | None = None
    evidence: dict[str, Any] | None = None
    definitive: bool = False


@dataclass(frozen=True)
class ModelInfo:
    """One listed model. ``efforts`` is empty when the provider did not say."""

    id: str
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None
    context_window: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "efforts": list(self.efforts), "default_effort": self.default_effort,
                "context_window": self.context_window}


@dataclass(frozen=True)
class LoginPrompt:
    """What to show the person: a URL to open, or a code to type at a URL."""

    kind: str
    url: str
    user_code: str | None = None
    verification_uri_complete: str | None = None
    browser_opened: bool = False
    browser_note: str = ""


class LoginError(RuntimeError):
    pass


class NotSignedIn(LookupError):
    pass


@dataclass
class ProviderSession:
    """One provider, one credential slot in the store.

    ``profile`` is the provider; ``store`` keeps the credential under
    ``profile.name``. ``http`` is the only way out to the network. ``open_browser``
    False keeps the sign-in URL in the prompt without launching anything.
    """

    profile: ProviderProfile
    store: CredentialStore
    http: HttpCall = field(default_factory=urllib_http)
    open_browser: bool = True
    login_timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS
    refresh_policy: RefreshPolicy = field(default_factory=RefreshPolicy)
    estimator: HostedTokenEstimator = field(default_factory=HostedTokenEstimator)
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    browser_opener: Callable[[str], Any] | None = None
    # Sent on every request unless the profile names its own; some auth servers refuse a library default.
    user_agent: str = "provider-session/1.0"

    # -- sign in / out ------------------------------------------------------

    def login(self, *, on_prompt: Callable[[LoginPrompt], None] | None = None,
              api_key: str | None = None) -> CredentialRecord:
        """Run the profile's flow to completion and store the credential."""
        auth = self.profile.auth
        if isinstance(auth, NoAuth):
            return self.store.put(self.profile.name, {"access_token": "", "token_type": "none"}, {"auth_kind": "none"})
        if isinstance(auth, ApiKeyAuth):
            if not api_key:
                raise LoginError(f"{self.profile.display_name} needs an API key")
            return self.store.put(self.profile.name, {"access_token": api_key, "token_type": "api_key"},
                                  {"auth_kind": "api_key"})
        if isinstance(auth, OAuthPkceFlow):
            token = self._login_pkce(auth, on_prompt)
        elif isinstance(auth, DeviceCodeFlow):
            token = self._login_device(auth, on_prompt)
        elif isinstance(auth, AuthorizedUserFileAuth):
            return self._login_authorized_user_file(auth)
        else:
            raise LoginError(f"unsupported auth kind {auth.kind!r}")
        return self._store_token(token, auth_kind=auth.kind)

    def status(self) -> dict[str, Any]:
        """Public view: signed in or not, expiry, account, quarantine.

        A ``NoAuth`` profile has nothing to sign into: it reads as signed in whenever its
        endpoint answers (``reachable``), so the rest of the vocabulary still applies.
        """
        if isinstance(self.profile.auth, NoAuth):
            reachable, why = self.reachable()
            return {"provider": self.profile.name, "display_name": self.profile.display_name, "auth_kind": "none",
                    "signed_in": reachable, "reachable": reachable, "reason": why, "metadata": {},
                    "quarantined": False, "quarantine_reason": None}
        record = self.store.peek(self.profile.name)
        view: dict[str, Any] = {"provider": self.profile.name, "display_name": self.profile.display_name,
                                "auth_kind": self.profile.auth.kind, "signed_in": record is not None and record.usable}
        if record is not None:
            view.update(record.public())
            expires = record.metadata.get("expires_at")
            if expires:
                view["expired"] = self.refresh_policy.needs_refresh(datetime.fromisoformat(expires), self.clock())
        return view

    def logout(self) -> bool:
        return self.store.delete(self.profile.name)

    def reachable(self, *, timeout_seconds: float = 5.0) -> tuple[bool, str]:
        """Does the endpoint answer at all? Any HTTP status but 503 counts; a socket error does not."""
        url = self.profile.models_url or self.profile.api_base_url
        try:
            response = self.http("GET", url, self._headers({"Accept": "application/json"}), None, timeout_seconds)
        except (OSError, URLError, TimeoutError) as error:
            return False, f"{type(error).__name__}: {error}"
        if response.status == 503:
            return False, "http 503: still loading"
        return True, f"http {response.status}"

    # -- the transport --------------------------------------------------------

    def credential(self) -> CredentialRecord:
        if isinstance(self.profile.auth, NoAuth):
            return CredentialRecord(self.profile.name, {"access_token": ""}, {"auth_kind": "none"})
        record = self.store.get(self.profile.name)
        if record is None:
            raise NotSignedIn(f"not signed in to {self.profile.display_name}")
        return record

    def transport(self, *, session_id: str | None = None) -> ProviderTransport:
        """A ``(path, payload) -> WireResponse`` callable for a model client; one conversation id per call."""
        return ProviderTransport(self, session_id=session_id)

    def count(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Prompt-token estimate in the shape a model client's ``count`` returns."""
        return self.estimator.estimate(payload).as_dict()

    def list_model_info(self) -> list[ModelInfo]:
        """What the provider reports for this credential: ids and, when it says, effort ladders.

        Accepts the OpenAI ``{"data": [{"id"}]}`` shape, a ``{"models": [{"slug"|"id", "visibility"?,
        "supported_reasoning_levels"?, "default_reasoning_level"?, "context_window"?}]}`` catalogue
        (hidden entries dropped), a ``{"models": {id: {"info": {...}}}}`` map, or a
        ``{"publisherModels": [{"name": "publishers/x/models/<id>"}]}`` list. ``model_id_prefix`` is applied. Empty when the profile
        has no list endpoint; raises ``NotSignedIn`` / ``QuarantinedCredential`` without a usable
        credential and ``LookupError`` when the endpoint answers badly.
        """
        if self.profile.models_path is None:
            return []
        record = self._bearer().ensure_fresh()
        url = self.profile.models_url_for(record.metadata) or ""
        headers = self._headers({"Accept": "application/json"})
        headers.update(self.profile.headers_for(record.secret["access_token"], record.metadata))
        response = self.http("GET", url, headers, None, self.profile.timeout_seconds)
        if not 200 <= response.status < 300:
            raise LookupError(f"model list http {response.status}: {response.body[:200].decode('utf-8', 'replace')}")
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except ValueError as error:
            raise LookupError("model list is not JSON") from error
        infos = _model_infos(payload)
        prefix = self.profile.model_id_prefix
        if prefix:
            infos = [ModelInfo(prefix + i.id if not i.id.startswith(prefix) else i.id, i.efforts, i.default_effort, i.context_window)
                     for i in infos]
        return infos

    def list_models(self) -> list[str]:
        """Just the ids of ``list_model_info``."""
        return [info.id for info in self.list_model_info()]

    def endpoint(self) -> dict[str, Any]:
        """Fields for an ``EndpointConfig``: where the transport already sends things."""
        return {"base_url": self.profile.api_base_url, "completion_path": self.profile.completion_path,
                "timeout_seconds": self.profile.timeout_seconds, "headers": dict(self.profile.static_headers)}

    # -- flows ---------------------------------------------------------------

    def _login_pkce(self, auth: OAuthPkceFlow, on_prompt: Callable[[LoginPrompt], None] | None) -> OAuthTokenRecord:
        with SingleRequestCapture(auth.redirect_path, host=auth.redirect_host, port=auth.redirect_port) as capture:
            redirect_uri = auth.redirect_uri if auth.redirect_port else capture.url
            config = OAuthPkceConfig(auth.authorization_endpoint, auth.token_endpoint, auth.client_id,
                                     redirect_uri, auth.scopes)
            authorization = create_pkce_authorization()
            url = build_authorization_url(config, authorization)
            if auth.extra_authorization_params:
                url += "&" + urlencode(auth.extra_authorization_params)
            self._prompt(on_prompt, LoginPrompt("browser", url))
            target = capture.wait(self.login_timeout_seconds)
        callback = parse_oauth_callback("http://" + auth.redirect_host + target, expected_path=auth.redirect_path,
                                        expected_state=authorization.state)
        exchange = build_token_exchange_request(config, code=callback.code, code_verifier=authorization.code_verifier)
        status, payload = self._post_form(exchange.url, exchange.headers, exchange.body)
        if status != 200 or payload.get("error"):
            raise LoginError(str(payload.get("error_description") or payload.get("error") or f"token exchange http {status}"))
        return normalize_oauth_token_response(payload, received_at=self.clock())

    def _login_device(self, auth: DeviceCodeFlow, on_prompt: Callable[[LoginPrompt], None] | None) -> OAuthTokenRecord:
        authorization_fields = list(auth.extra_authorization_params.items())
        token_fields = list(auth.extra_token_params.items())
        if auth.pkce:
            pair = create_pkce_authorization()
            authorization_fields += [("code_challenge", pair.code_challenge), ("code_challenge_method", "S256")]
            token_fields.append(("code_verifier", pair.code_verifier))
        config = DeviceCodeConfig(auth.device_authorization_endpoint, auth.token_endpoint, auth.client_id, auth.scopes,
                                  tuple(authorization_fields), tuple(token_fields))

        def post(request: RequestDescriptor) -> tuple[int, dict[str, Any]]:
            return self._post_form(request.url, request.headers, request.body)

        def show(device: Any) -> None:
            self._prompt(on_prompt, LoginPrompt("device", device.verification_uri_complete or device.verification_uri,
                                                user_code=device.user_code,
                                                verification_uri_complete=device.verification_uri_complete))

        try:
            payload = run_device_flow(config, post, sleep=self.sleep, clock=lambda: self.clock().timestamp(),
                                      on_authorization=show)
        except DeviceFlowError as error:
            raise LoginError(str(error)) from error
        return normalize_oauth_token_response(payload, received_at=self.clock())

    def _login_authorized_user_file(self, auth: AuthorizedUserFileAuth) -> CredentialRecord:
        """Adopt the refresh token a vendor's own login wrote, then mint the first access token."""
        import os
        from pathlib import Path

        candidates = [os.environ.get(auth.environment_variable or "") or "", auth.path]
        path = next((Path(c).expanduser() for c in candidates if c and Path(c).expanduser().is_file()), None)
        if path is None:
            raise LoginError(f"no credentials file at {auth.path}; run the vendor's login first")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise LoginError(f"{path} is not JSON") from error
        if data.get("type") != auth.expected_type:
            raise LoginError(f"{path} is a {data.get('type')!r} credential; only {auth.expected_type!r} is supported here")
        missing = [k for k in ("client_id", "client_secret", "refresh_token") if not data.get(k)]
        if missing:
            raise LoginError(f"{path} lacks {', '.join(missing)}")
        seed = CredentialRecord(self.profile.name, {"access_token": "", "refresh_token": data["refresh_token"],
                                                    "client_id": data["client_id"], "client_secret": data["client_secret"]},
                                {"auth_kind": auth.kind, "source_file": str(path)})
        self.store.put(seed.key, seed.secret, seed.metadata)
        return self._refresh(seed)

    def _prompt(self, on_prompt: Callable[[LoginPrompt], None] | None, prompt: LoginPrompt) -> None:
        if self.open_browser:
            if self.browser_opener is not None:
                self.browser_opener(prompt.url)
                prompt = LoginPrompt(prompt.kind, prompt.url, prompt.user_code, prompt.verification_uri_complete, True)
            else:
                launched = open_url(prompt.url)
                prompt = LoginPrompt(prompt.kind, prompt.url, prompt.user_code, prompt.verification_uri_complete,
                                     launched.opened, launched.reason)
        if on_prompt is not None:
            on_prompt(prompt)

    # -- credential plumbing -------------------------------------------------

    def _store_token(self, token: OAuthTokenRecord, *, auth_kind: str, extra_secret: dict[str, Any] | None = None,
                     extra_metadata: dict[str, Any] | None = None) -> CredentialRecord:
        metadata: dict[str, Any] = {"auth_kind": auth_kind, "scopes": list(token.scopes)}
        metadata.update(extra_metadata or {})
        if token.expires_at is not None:
            metadata["expires_at"] = token.expires_at.isoformat()
        for name in self.profile.token_metadata_fields:
            value = (token.raw or {}).get(name)
            if value not in (None, ""):
                metadata[name] = value
        metadata.update(self._claims_metadata(token))
        secret = {"access_token": token.access_token, "token_type": token.token_type, **(extra_secret or {})}
        if token.refresh_token:
            secret["refresh_token"] = token.refresh_token
        if token.id_token:
            secret["id_token"] = token.id_token
        return self.store.put(self.profile.name, secret, metadata)

    def _claims_metadata(self, token: OAuthTokenRecord) -> dict[str, Any]:
        auth = self.profile.auth
        if not isinstance(auth, OAuthPkceFlow) or not token.id_token:
            return {}
        try:
            claims = unverified_claims(token.id_token)
        except MalformedJwt:
            return {}
        metadata: dict[str, Any] = {}
        for key in ("email", "sub"):
            if claims.get(key):
                metadata[key] = claims[key]
        if auth.account_claim_namespace and auth.account_claim_key:
            account = namespaced_claim(claims, auth.account_claim_namespace, auth.account_claim_key)
            if account:
                metadata["account_id"] = account
        return metadata

    def _refresh(self, record: CredentialRecord) -> CredentialRecord:
        auth = self.profile.auth
        refresh_token = record.secret.get("refresh_token")
        if isinstance(auth, NoAuth):
            raise RefreshFailed("endpoint refused the request; it takes no credential", terminal=False)
        if isinstance(auth, ApiKeyAuth) or not refresh_token:
            failure = RefreshFailed("credential cannot be refreshed; sign in again", terminal=True)
            self.store.quarantine(self.profile.name, str(failure))
            raise failure
        if isinstance(auth, AuthorizedUserFileAuth):
            body = urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token,
                              "client_id": record.secret.get("client_id", ""), "client_secret": record.secret.get("client_secret", "")})
            status, payload = self._post_form(auth.token_endpoint, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        else:
            config = OAuthPkceConfig("", auth.token_endpoint, auth.client_id, "")
            request = build_refresh_token_request(config, refresh_token=refresh_token)
            status, payload = self._post_form(request.url, request.headers, request.body)
        if status != 200 or payload.get("error") or not payload.get("access_token"):
            failure = classify_refresh_failure(status, payload)
            if failure.terminal:
                self.store.quarantine(self.profile.name, f"{failure.error_code or status}: {failure}")
            raise failure
        if not payload.get("refresh_token"):
            payload = dict(payload, refresh_token=refresh_token)
        token = normalize_oauth_token_response(payload, received_at=self.clock())
        keep = {k: v for k, v in record.secret.items() if k in ("client_id", "client_secret")}
        carry = {k: v for k, v in record.metadata.items() if k in self.profile.token_metadata_fields or k == "source_file"}
        return self._store_token(token, auth_kind=auth.kind, extra_secret=keep, extra_metadata=carry)

    def _bearer(self) -> BearerSession[CredentialRecord]:
        def expires_at(record: CredentialRecord) -> datetime | None:
            value = record.metadata.get("expires_at")
            return datetime.fromisoformat(value) if value else None

        return BearerSession(self.credential(), self._refresh, expires_at, policy=self.refresh_policy, clock=self.clock)

    def _headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        merged = {"User-Agent": self.user_agent}
        merged.update(headers)
        return merged

    def _post_form(self, url: str, headers: Mapping[str, str], body: str) -> tuple[int, dict[str, Any]]:
        response = self.http("POST", url, self._headers(headers), body.encode("utf-8"), self.profile.timeout_seconds)
        try:
            payload = json.loads(response.body.decode("utf-8") or "{}")
        except ValueError:
            payload = {"error": f"http_{response.status}", "error_description": response.body[:200].decode("utf-8", "replace")}
        return response.status, payload if isinstance(payload, dict) else {}


class ProviderTransport:
    """The callable a model client posts through. Chat Completions in, Chat Completions out."""

    def __init__(self, session: ProviderSession, *, session_id: str | None = None) -> None:
        self.session = session
        self.profile = session.profile
        # One conversation id per transport; a profile's header template may cite it as {session}.
        self.session_id = session_id or uuid4().hex

    def request_metadata(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """What changed on the wire, with no authentication material."""
        metadata = {"provider": self.profile.name, "wire": self.profile.wire, "url": self._url(path)}
        codec = {"responses": chat_to_responses, "messages": chat_to_messages}.get(self.profile.wire)
        if codec is not None:
            try:
                metadata["dropped_fields"] = list(codec(payload).dropped_fields)
            except ValueError as error:
                metadata["codec_error"] = str(error)
        return metadata

    def __call__(self, path: str, payload: dict[str, Any], *, timeout_seconds: float | None = None) -> WireResponse:
        started = time.monotonic()
        # The caller's bound, else the one set on this transport (a model client's endpoint timeout), else the
        # profile's: a stalled proxy response held a worker turn for fifteen minutes on the profile's 900 s while
        # the config said 300.
        named = timeout_seconds if timeout_seconds is not None else getattr(self, "default_timeout_seconds", None)
        timeout = self.profile.timeout_seconds if named is None else min(named, self.profile.timeout_seconds)
        try:
            bearer = self.session._bearer()
        except (NotSignedIn, QuarantinedCredential, RefreshFailed) as error:
            return WireResponse(None, "", time.monotonic() - started, f"{type(error).__name__}: {error}",
                                failure_kind="authentication", definitive=True)
        body_bytes, error = self._encode(payload)
        if error:
            return WireResponse(None, "", time.monotonic() - started, error, failure_kind="codec", definitive=True)

        def send(record: CredentialRecord) -> tuple[int, HttpResponse | str]:
            headers = self.session._headers(JSON_HEADERS)
            headers.update(self.profile.headers_for(record.secret["access_token"], dict(record.metadata, session=self.session_id)))
            on_progress = getattr(self, "on_progress", None)
            if on_progress is not None:
                try:
                    self.session.http.progress = lambda total, last_event: on_progress(dict(bytes=total, last_event=last_event, at=time.time()))
                except AttributeError:
                    pass
            try:
                response = self.session.http("POST", self._url(path), headers, body_bytes, timeout)
            except (OSError, URLError, TimeoutError) as exc:
                return 0, f"{type(exc).__name__}: {exc}"
            return response.status, response

        try:
            status, result = bearer.call(send)
        except RefreshFailed as failure:
            return WireResponse(401, "", time.monotonic() - started, f"RefreshFailed: {failure}",
                                failure_kind="authentication", definitive=failure.terminal)
        elapsed = time.monotonic() - started
        if isinstance(result, str):
            return WireResponse(None, "", elapsed, result)
        return self._decode(result, elapsed, payload)

    def _url(self, path: str) -> str:
        record = self.session.store.peek(self.profile.name)
        return self.profile.base_url_for(record.metadata if record else None).rstrip("/") + path

    def _encode(self, payload: dict[str, Any]) -> tuple[bytes, str | None]:
        body = dict(payload)
        if not body.get("model") and self.profile.default_model:
            body["model"] = self.profile.default_model
        if self.profile.wire in ("responses", "messages"):
            try:
                body = (chat_to_responses if self.profile.wire == "responses" else chat_to_messages)(body).body
            except ValueError as error:
                return b"", f"CodecError: {error}"
        for key in getattr(self.profile, "unsupported_fields", ()):
            body.pop(key, None)   # the backend answers 400 to these; the profile says so
        for old, new in getattr(self.profile, "renamed_fields", {}).items():
            if old in body and new not in body:
                body[new] = body.pop(old)   # the backend spells the field differently
        return json.dumps(body, allow_nan=False).encode("utf-8"), None

    def _decode(self, response: HttpResponse, elapsed: float, payload: dict[str, Any]) -> WireResponse:
        text = response.body.decode("utf-8", errors="replace")
        if not 200 <= response.status < 300:
            return WireResponse(response.status, text, elapsed)
        streamed = (response.headers.get("content-type", "").startswith("text/event-stream")
                    or text.lstrip().startswith(("event:", "data:")))
        if self.profile.wire == "chat_completions":
            chat = _json_object(text)
        elif self.profile.wire == "messages":
            if streamed:
                chat = fold_messages_sse(text.splitlines(keepends=True))
            else:
                chat = _json_object(text)
                if chat is not None and "choices" not in chat:
                    chat = messages_to_chat(chat)
        elif streamed:
            chat = fold_sse(text.splitlines(keepends=True))
        else:
            chat = _json_object(text)
            if chat is not None and "choices" not in chat:
                chat = responses_to_chat(chat)
        if chat is None:
            return WireResponse(response.status, text, elapsed, "Response is not a JSON object")
        usage = chat.get("usage") or {}
        if isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] > 0:
            self.session.estimator.calibrate(payload, usage["prompt_tokens"])
        if chat.get("error") and chat.get("choices", [{}])[0].get("finish_reason") == "error":
            return WireResponse(response.status, json.dumps(chat), elapsed, str(chat["error"].get("message") or chat["error"]),
                                failure_kind="provider_error")
        return WireResponse(response.status, json.dumps(chat), elapsed)


def _model_infos(payload: Any) -> list[ModelInfo]:
    rows: Any = payload
    if isinstance(payload, dict):
        rows = payload.get("data")
        if rows is None:
            rows = payload.get("models")
        if rows is None:
            rows = payload.get("publisherModels")
        if isinstance(rows, dict):
            rows = [dict(value.get("info") or value, id=key) if isinstance(value, dict) else {"id": key}
                    for key, value in rows.items()]
    if not isinstance(rows, list):
        raise LookupError("model list has no data or models array")
    infos: list[ModelInfo] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("visibility") in ("hide", "none") or row.get("hidden") is True:
            continue
        identifier = row.get("id") or row.get("slug") or row.get("name")
        if not identifier:
            continue
        identifier = str(identifier)
        if "/models/" in identifier:
            identifier = identifier.rsplit("/models/", 1)[1]   # publishers/x/models/<id>
        elif identifier.startswith("models/"):
            identifier = identifier.split("/", 1)[1]
        efforts = _efforts(row.get("supported_reasoning_levels") or row.get("reasoning_efforts"))
        default = row.get("default_reasoning_level") or row.get("reasoning_effort")
        if row.get("supports_reasoning_effort") is False:
            efforts, default = (), None
        context = row.get("context_window") or row.get("context_length")
        infos.append(ModelInfo(identifier, efforts, str(default) if default in efforts else None,
                               int(context) if isinstance(context, (int, float)) and context > 0 else None))
    return infos


def _efforts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    found: list[str] = []
    for item in value:
        name = item.get("effort") or item.get("id") or item.get("value") if isinstance(item, dict) else item
        if isinstance(name, str) and name and name not in found:
            found.append(name)
    return tuple(found)


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None

