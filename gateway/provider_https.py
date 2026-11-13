# Károly Tamás szakdolgozati prototípusa (2026).
"""Opt-in HTTPS transport for an already approved provider request."""

from contextlib import closing
import http.client
from ipaddress import ip_address
import json
from queue import Empty, Queue
import re
import socket
import ssl
from threading import BoundedSemaphore, Thread, Timer
from time import monotonic
from urllib.parse import urlsplit

from gateway.operator_config import ModelProfile


class TransportBlocked(PermissionError):
    """A safe, credential-free transport failure."""


_PATHS = {
    "openai-responses": "/responses",
    "openai-chat-compatible": "/chat/completions",
    "anthropic-messages": "/messages",
}
_DNS_SLOTS = BoundedSemaphore(4)
_MAX_RESPONSE = 256_000


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _resolve(host: str, port: int, deadline: float):
    result = Queue(maxsize=1)

    def lookup():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            result.put([])
        finally:
            _DNS_SLOTS.release()

    if not _DNS_SLOTS.acquire(blocking=False):
        raise TransportBlocked("A névfeloldás átmenetileg nem érhető el.")
    try:
        Thread(target=lookup, daemon=True).start()
    except RuntimeError:
        _DNS_SLOTS.release()
        raise TransportBlocked("A névfeloldás átmenetileg nem érhető el.") from None
    try:
        records = result.get(timeout=_remaining(deadline))
    except Empty:
        raise TimeoutError from None
    if not records:
        raise TransportBlocked("A modellvégpont nem oldható fel.")
    for family, _, _, _, address in records:
        target = ip_address(address[0])
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or not target.is_global or target.is_multicast or target.is_reserved
            or (target.version == 6 and (target.ipv4_mapped is not None or target.sixtofour is not None))
        ):
            raise TransportBlocked("A végpont feloldása tiltott hálózati címre mutat.")
    family, _, _, _, address = records[0]
    return family, address


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, family, address, deadline: float):
        super().__init__(host, port, context=ssl.create_default_context())
        self._family = family
        self._address = address
        self._deadline = deadline
        self._timer: Timer | None = None
        self._deadline_socket: socket.socket | None = None

    def _abort(self):
        if self._deadline_socket is not None:
            try:
                self._deadline_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def connect(self):
        raw = socket.socket(self._family, socket.SOCK_STREAM)
        try:
            raw.settimeout(_remaining(self._deadline))
            raw.connect(self._address)
            raw.settimeout(_remaining(self._deadline))
            self._deadline_socket = raw.dup()
            self._timer = Timer(_remaining(self._deadline), self._abort)
            self._timer.daemon = True
            self._timer.start()
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise

    def finish(self):
        if self._timer is not None:
            self._timer.cancel()
        if self._deadline_socket is not None:
            self._deadline_socket.close()
        super().close()


def _contains_secret(body: bytes, secret: str) -> bool:
    if secret.encode("ascii") in body:
        return True
    value = json.loads(body)
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str) and secret in item:
            return True
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


class ProviderHTTPS:
    def __init__(self, allowed_targets: frozenset[tuple[str, str]] = frozenset()):
        """Allow exact (protocol, HTTPS base URL) pairs selected outside the request."""
        self._allowed_targets = frozenset(allowed_targets)

    def allows(self, profile: ModelProfile) -> bool:
        return (profile.protocol, profile.endpoint) in self._allowed_targets

    def send(self, profile: ModelProfile, body: bytes, *, api_key: str | None = None) -> bytes:
        if not self.allows(profile):
            raise TransportBlocked("A szolgáltatói célcím nincs jóváhagyva.")
        deadline = monotonic() + profile.timeout_seconds
        try:
            url = urlsplit(profile.endpoint)
            if (
                url.scheme != "https" or not url.hostname or url.port not in (None, 443)
                or url.username is not None or url.password is not None or url.query or url.fragment
                or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", url.path)
            ):
                raise TransportBlocked("A szolgáltatói HTTPS-cím érvénytelen.")
            host = url.hostname.encode("idna").decode("ascii")
            if "." not in host.rstrip(".") or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
                raise TransportBlocked("A szolgáltatói gépnév érvénytelen.")
            try:
                ip_address(host.rstrip("."))
            except ValueError:
                pass
            else:
                raise TransportBlocked("A szolgáltatói célhoz DNS-név szükséges.")
            path = url.path.rstrip("/") + _PATHS[profile.protocol]
            if profile.auth == "api-key":
                if not api_key or not re.fullmatch(r"[!-~]{1,2048}", api_key):
                    raise TransportBlocked("A szolgáltatói hitelesítés nem elérhető.")
                if _contains_secret(body, api_key):
                    raise TransportBlocked("A kérés bizalmas hitelesítési adatot tartalmaz.")
            elif profile.auth != "none" or api_key is not None:
                raise TransportBlocked("A szolgáltatói hitelesítés nem elérhető.")
            family, address = _resolve(host, 443, deadline)
            headers = {"Content-Type": "application/json", "Accept": "application/json", "Connection": "close"}
            if profile.protocol == "anthropic-messages":
                headers["anthropic-version"] = "2026-10-21"
            if api_key is not None:
                headers["Authorization"] = f"Bearer {api_key}"
            connection = _PinnedHTTPSConnection(host, 443, family, address, deadline)
            try:
                connection.request("POST", path, body=body, headers=headers)
                with closing(connection.getresponse()) as response:
                    if response.status != 200:
                        raise TransportBlocked("A szolgáltató nem adott sikeres választ.")
                    result = response.read(_MAX_RESPONSE + 1)
            finally:
                connection.finish()
            _remaining(deadline)
            if len(result) > _MAX_RESPONSE:
                raise TransportBlocked("A szolgáltatói válasz túl nagy.")
            if api_key is not None and _contains_secret(result, api_key):
                raise TransportBlocked("A szolgáltatói válasz bizalmas hitelesítési adatot tartalmaz.")
            return result
        except TransportBlocked:
            raise
        except TimeoutError:
            raise TransportBlocked("A szolgáltatói kérés időkorlátja lejárt.") from None
        except (OSError, ValueError, KeyError, TypeError, RecursionError, http.client.HTTPException):
            raise TransportBlocked("A szolgáltatói HTTPS-átvitel sikertelen.") from None
