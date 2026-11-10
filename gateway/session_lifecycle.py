"""Process-local session expiry and ownership of temporary token mappings."""

from collections.abc import Callable
from dataclasses import dataclass, field
from secrets import token_hex
from threading import Lock
from time import monotonic
from types import MappingProxyType

from gateway.text_processing import CanonicalText, Selection, mask_request
from gateway.token_mapping import (
    RequestBinding, TokenSelection, TokenizedRequest, reject_reserved_markers, tokenize_request,
)


class SessionUnavailable(ValueError):
    """The session or request is unavailable or no longer current."""


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    binding: RequestBinding = field(repr=False)
    source: str = field(repr=False)
    request: str = field(repr=False)
    tokens: frozenset[str] = field(repr=False)


@dataclass(slots=True, repr=False)
class _Session:
    opened: float
    last_activity: float
    requests: dict[str, TokenizedRequest] = field(default_factory=dict)


class SessionStore:
    """Own mappings without exposing them to callers or persisting them.

    Expiry is enforced on use; sweep can also be called by a later scheduler.
    Dropping references is not physical erasure of process memory.
    """

    IDLE_SECONDS = 15 * 60
    MAX_SECONDS = 60 * 60

    def __init__(self, *, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = Lock()

    def _expired(self, session: _Session, now: float) -> bool:
        return (
            now >= session.last_activity + self.IDLE_SECONDS
            or now >= session.opened + self.MAX_SECONDS
        )

    def _active(self, session_id: str, now: float) -> _Session:
        session = self._sessions.get(session_id)
        if session is not None and self._expired(session, now):
            del self._sessions[session_id]
            session = None
        if session is None:
            raise SessionUnavailable("Session unavailable")
        return session

    def _sweep(self, now: float) -> int:
        expired = [key for key, session in self._sessions.items() if self._expired(session, now)]
        for key in expired:
            del self._sessions[key]
        return len(expired)

    def sweep(self) -> int:
        with self._lock:
            return self._sweep(self._clock())

    def is_active(self, session_id: str) -> bool:
        """Check liveness without extending the inactivity deadline."""
        with self._lock:
            try:
                self._active(session_id, self._clock())
            except SessionUnavailable:
                return False
            return True

    def is_current(self, binding: RequestBinding) -> bool:
        """Check that the exact request is still present in its live session."""
        with self._lock:
            try:
                session = self._active(binding.session_id, self._clock())
            except SessionUnavailable:
                return False
            mapping = session.requests.get(binding.request_id)
            return mapping is not None and mapping.binding == binding

    def open_session(self) -> str:
        with self._lock:
            now = self._clock()
            self._sweep(now)
            session_id = token_hex(16)
            while session_id in self._sessions:
                session_id = token_hex(16)
            self._sessions[session_id] = _Session(now, now)
            return session_id

    def prepare(
        self,
        session_id: str,
        source: CanonicalText,
        request: CanonicalText,
        *,
        source_selections: tuple[TokenSelection, ...] = (),
        request_selections: tuple[TokenSelection, ...] = (),
        check_residue: bool = False,
    ) -> PreparedRequest:
        def build(binding: RequestBinding) -> TokenizedRequest:
            mapping = tokenize_request(
                source, request, binding=binding,
                source_selections=source_selections, request_selections=request_selections,
            )
            if check_residue:
                mapping.require_no_residue()
            return mapping

        return self._prepare(session_id, source, request, build)

    def prepare_masked(
        self,
        session_id: str,
        source: CanonicalText,
        request: CanonicalText,
        *,
        source_selections: tuple[Selection, ...],
        request_selections: tuple[Selection, ...],
    ) -> PreparedRequest:
        def build(binding: RequestBinding) -> TokenizedRequest:
            reject_reserved_markers(source.text)
            reject_reserved_markers(request.text)
            masked_source, masked_request = mask_request(
                source, request, source_selections=source_selections,
                request_selections=request_selections,
            )
            return TokenizedRequest(masked_source, masked_request, binding, MappingProxyType({}))

        return self._prepare(session_id, source, request, build)

    def _prepare(
        self,
        session_id: str,
        source: CanonicalText,
        request: CanonicalText,
        build: Callable[[RequestBinding], TokenizedRequest],
    ) -> PreparedRequest:
        with self._lock:
            session = self._active(session_id, self._clock())
            request_id = token_hex(16)
            while request_id in session.requests:
                request_id = token_hex(16)
            binding = RequestBinding(session_id, request_id, source.digest, request.digest)
            mapping = build(binding)
            now = self._clock()
            self._active(session_id, now)
            session.requests[request_id] = mapping
            session.last_activity = now
            return PreparedRequest(binding, mapping.source, mapping.request, mapping.tokens)

    def restore(
        self, session_id: str, binding: RequestBinding, response: str, *, allowed: frozenset[str]
    ) -> str:
        with self._lock:
            session = self._active(session_id, self._clock())
            mapping = session.requests.get(binding.request_id)
            if mapping is None or binding != mapping.binding:
                raise SessionUnavailable("Request unavailable")
            result = mapping.restore(response, binding=binding, allowed=allowed)
            now = self._clock()
            self._active(session_id, now)
            session.last_activity = now
            return result

    def close_request(self, session_id: str, binding: RequestBinding) -> None:
        with self._lock:
            now = self._clock()
            session = self._active(session_id, now)
            mapping = session.requests.get(binding.request_id)
            if mapping is None or binding != mapping.binding:
                raise SessionUnavailable("Request unavailable")
            del session.requests[binding.request_id]
            session.last_activity = now

    def close_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
