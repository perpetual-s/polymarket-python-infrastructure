"""Bounded credential redaction for exception and logging boundaries.

The functions in this module intentionally return safe representations rather
than preserving every input type.  Their outputs are persisted in exceptions
and logs, where preventing an object's ``repr``/``str`` from leaking a secret
is more important than retaining an opaque runtime object.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID


REDACTED = "[REDACTED]"
_CYCLE = "[CYCLE]"
_MAX_DEPTH_REACHED = "[MAX_DEPTH]"
_NODE_LIMIT_REACHED = "[NODE_LIMIT]"
_TRUNCATED = "[TRUNCATED]"

# Redaction is a logging/error-boundary operation.  Bound all attacker-shaped
# input so a diagnostic path cannot recurse forever or create an unbounded log.
_MAX_DEPTH = 12
_MAX_CONTAINER_ITEMS = 256
_MAX_NODES = 2_048
_MAX_TEXT_LENGTH = 65_536
_MAX_FIELD_NAME_LENGTH = 256

_URI_CREDENTIAL_PATTERN = re.compile(
    # The username may be empty (redis://:password@host is the standard form),
    # so the pre-colon group is optional; the ``:secret@`` structure is what
    # marks the authority as credential-bearing.
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)"
    r"[^:/?#\s@]*:[^/?#\s@]*@",
    re.IGNORECASE,
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"(?<![0-9a-f])0x[0-9a-f]{64}(?![0-9a-f])",
    re.IGNORECASE,
)
_ETHEREUM_ADDRESS_PATTERN = re.compile(r"0x[0-9a-f]{40}", re.IGNORECASE)
_LABELED_SECRET_PATTERN = re.compile(
    r"(?P<prefix>\b(?:[a-z0-9]+[_-])*(?:"
    r"private[_-]?key|api[_-]?(?:key|secret|passphrase|credentials)|"
    r"secret[_-]?(?:access[_-]?)?key|secret|passphrase|password|"
    r"access[_-]?(?:key|token)|refresh[_-]?token|session[_-]?token|"
    r"bearer[_-]?token|csrf[_-]?token|id[_-]?token|token|"
    r"auth(?:orization|[_-]?token)|credentials?|cookie|clob[_-]?auth|"
    r"seed[_-]?phrase|mnemonic|"
    r"dsn|database[_-]?url"
    r")\b[\"']?\s*[:=]\s*)"
    r"(?:"
    r"(?P<quote>[\"'])(?!\[REDACTED\])(?P<quoted_value>.*?)(?P=quote)|"
    # The unquoted value is greedy so a secret containing ``:`` or ``)`` (for
    # example a DSN or a ``user:pass`` pair) is redacted whole rather than only
    # up to the first inner separator.  An HTTP auth scheme keyword plus one
    # space-separated credential token (``Basic <b64>``) is treated as part of
    # the value so the credential itself is never left behind.
    r"(?!\[REDACTED\])"
    r"(?P<value>(?:(?:Bearer|Basic|Digest|Token)\s+)?[^\"'\s,;}\]]+)"
    r")(?=$|[\s,;}\]]|[\"'])",
    re.IGNORECASE,
)
_BASE64_SECRET_PATTERN = re.compile(
    # Standard base64 alphabet only. The URL-safe extension (``-``/``_``) is
    # deliberately NOT included here: it cannot be distinguished by pattern from
    # a long kebab/snake public identifier (e.g. a 40+ char market slug), so
    # widening it over-redacts core public data. URL-safe secrets are instead
    # caught by the labeled pattern (``api_secret=<value>``) and by structured
    # ``redact_value`` on credential fields/containers, which are format-agnostic;
    # a bare, unlabeled URL-safe secret in free text is a logging anti-pattern
    # the signer/auth boundaries already suppress with ``from None``.
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])"
)

_SENSITIVE_FIELD_SUFFIXES = frozenset(
    {
        "privatekey",
        "secretkey",
        "apikey",
        "apisecret",
        "apipassphrase",
        "secret",
        "passphrase",
        "password",
        "accesstoken",
        "authorization",
        "authtoken",
        "refreshtoken",
        "sessiontoken",
        "bearertoken",
        "csrftoken",
        "idtoken",
        "token",
        "accesskey",
        "secretaccesskey",
        "dsn",
        "databaseurl",
        "cookie",
        "credential",
        "seedphrase",
        "mnemonic",
    }
)
_SENSITIVE_CONTAINER_SUFFIXES = frozenset(
    {
        "credentials",
        "credential",
        "apicredentials",
        "clobauth",
        "cookie",
    }
)
_PUBLIC_IDENTIFIER_SUFFIXES = frozenset(
    {
        "address",
        "conditionid",
        "marketid",
        "orderid",
        "tokenid",
    }
)
_STRONGLY_SENSITIVE_NAME_PARTS = frozenset(
    {
        "privatekey",
        "apisecret",
        "apipassphrase",
        "secretaccesskey",
        "databaseurl",
        "secretkey",
        "seedphrase",
        "mnemonic",
    }
)


@dataclass
class _RedactionState:
    active_ids: set[int]
    nodes_remaining: int = _MAX_NODES

    def consume_node(self) -> bool:
        if self.nodes_remaining <= 0:
            return False
        self.nodes_remaining -= 1
        return True


def _normalized_field_name(value: Any) -> str:
    # Structured field names are strings.  Never call an attacker-controlled
    # mapping key's ``str`` method, and never run a regex over an unbounded key.
    if not isinstance(value, str) or len(value) > _MAX_FIELD_NAME_LENGTH:
        return ""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _matches_suffix(value: str, suffixes: frozenset[str]) -> bool:
    return any(value == suffix or value.endswith(suffix) for suffix in suffixes)


def _is_sensitive_field(value: Any) -> bool:
    normalized = _normalized_field_name(value)
    return _matches_suffix(normalized, _SENSITIVE_FIELD_SUFFIXES) or any(
        part in normalized for part in _STRONGLY_SENSITIVE_NAME_PARTS
    )


def _is_sensitive_container(value: Any) -> bool:
    return _matches_suffix(
        _normalized_field_name(value),
        _SENSITIVE_CONTAINER_SUFFIXES,
    )


def _is_public_identifier_field(value: Any) -> bool:
    normalized = _normalized_field_name(value)
    return not _is_sensitive_field(value) and _matches_suffix(
        normalized,
        _PUBLIC_IDENTIFIER_SUFFIXES,
    )


def _is_sensitive_object(value: Any) -> bool:
    name = _normalized_field_name(type(value).__name__)
    return any(marker in name for marker in ("credential", "credentials", "creds"))


def _replace_labeled_secret(match: re.Match[str]) -> str:
    quote = match.group("quote") or ""
    return f"{match.group('prefix')}{quote}{REDACTED}{quote}"


def _replace_base64_candidate(match: re.Match[str]) -> str:
    candidate = match.group(0)
    # A 20-byte Ethereum address is public and happens to satisfy the broad
    # base64 alphabet.  Ambiguous 32-byte 0x values remain fail-closed unless
    # their mapping/LogRecord field identifies them as a public ID.
    if _ETHEREUM_ADDRESS_PATTERN.fullmatch(candidate):
        return candidate
    return REDACTED


def redact_text(text: str, *, redact_unlabeled: bool = True) -> str:
    """Redact credentials while retaining non-secret diagnostic context.

    Unlabeled 32-byte hexadecimal/base64 values are treated as secrets.  Code
    that knows a value is a public condition/order/token ID must pass it in a
    typed mapping or LogRecord field so :func:`redact_value` can preserve it.
    """
    if not text:
        return text
    if len(text) > _MAX_TEXT_LENGTH:
        # Keeping a prefix could retain a partially truncated URI credential.
        return f"{_TRUNCATED}:{len(text)}"

    text = _LABELED_SECRET_PATTERN.sub(_replace_labeled_secret, text)
    text = _URI_CREDENTIAL_PATTERN.sub(r"\g<scheme>[REDACTED]@", text)
    if redact_unlabeled:
        text = _PRIVATE_KEY_PATTERN.sub("0x[REDACTED]", text)
        text = _BASE64_SECRET_PATTERN.sub(_replace_base64_candidate, text)
    return text


def _safe_mapping_key(key: Any) -> Any:
    if isinstance(key, str):
        return redact_text(key)
    if key is None or isinstance(key, (bool, int, float)):
        return key
    return f"<{type(key).__name__}>"


def _object_attributes(value: Any) -> Mapping[str, Any] | None:
    """Return inspectable state without invoking arbitrary properties/str."""
    try:
        dataclass_instance = is_dataclass(value) and not isinstance(value, type)
    except Exception:
        dataclass_instance = False
    if dataclass_instance:
        result: dict[str, Any] = {}
        for index, field in enumerate(fields(value)):
            if index >= _MAX_CONTAINER_ITEMS:
                result["__redaction_truncated__"] = _TRUNCATED
                break
            try:
                result[field.name] = getattr(value, field.name)
            except Exception:
                result[field.name] = "[UNAVAILABLE]"
        return result

    try:
        attributes = vars(value)
    except Exception:
        attributes = None
    if isinstance(attributes, Mapping):
        # Do not copy an unbounded ``__dict__`` before the recursive mapping
        # walker has a chance to enforce its item and node limits.
        return attributes

    try:
        slots = getattr(type(value), "__slots__", ())
    except Exception:
        slots = ()
    if isinstance(slots, str):
        slots = (slots,)
    if slots:
        result = {}
        try:
            iterator = iter(slots)
        except TypeError:
            iterator = iter(())
        for index, slot in enumerate(iterator):
            if index >= _MAX_CONTAINER_ITEMS:
                result["__redaction_truncated__"] = _TRUNCATED
                break
            if not isinstance(slot, str) or slot.startswith("__"):
                continue
            try:
                result[slot] = getattr(value, slot)
            except Exception:
                result[slot] = "[UNAVAILABLE]"
        return result or None
    return None


def _is_public_scalar(value: Any) -> bool:
    """Return whether a typed public-ID exemption is safe for this value."""
    return value is None or isinstance(
        value,
        (
            str,
            bytes,
            bytearray,
            memoryview,
            bool,
            int,
            float,
            datetime,
            date,
            time,
            Decimal,
            UUID,
            Path,
            Enum,
        ),
    )


def _redact_value(
    value: Any,
    *,
    redact_unlabeled: bool,
    force_secret: bool,
    depth: int,
    state: _RedactionState,
) -> Any:
    if not state.consume_node():
        return _NODE_LIMIT_REACHED
    if depth > _MAX_DEPTH:
        return _MAX_DEPTH_REACHED

    if value is None:
        return None
    if force_secret and isinstance(value, (str, bytes, bytearray, memoryview)):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value, redact_unlabeled=redact_unlabeled)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{type(value).__name__}:{len(value)}>"
    if isinstance(value, (bool, int, float)):
        return REDACTED if force_secret else value
    if isinstance(value, (datetime, date, time, Decimal, UUID, Path, Enum)):
        return REDACTED if force_secret else redact_text(str(value))
    try:
        secret_getter = getattr(value, "get_secret_value", None)
    except Exception:
        secret_getter = None
    if callable(secret_getter):
        return REDACTED

    if isinstance(value, BaseException):
        if force_secret:
            safe_message = REDACTED
        else:
            try:
                safe_message = redact_text(str(value))
            except Exception:
                safe_message = "[UNAVAILABLE]"
        return {
            "type": type(value).__name__[:_MAX_FIELD_NAME_LENGTH],
            "message": safe_message,
        }

    tracked_id = id(value)
    if tracked_id in state.active_ids:
        return _CYCLE
    state.active_ids.add(tracked_id)
    try:
        if isinstance(value, Mapping):
            sanitized: dict[Any, Any] = {}
            try:
                items = iter(value.items())
                for index, (key, item) in enumerate(items):
                    if index >= _MAX_CONTAINER_ITEMS:
                        sanitized["__redaction_truncated__"] = _TRUNCATED
                        break
                    safe_key = _safe_mapping_key(key)
                    sensitive_field = _is_sensitive_field(key)
                    sensitive_container = _is_sensitive_container(key)
                    # Public-ID typing is a narrow scalar exemption.  It must
                    # never disable inherited secret-container semantics or
                    # bless an entire nested object graph as public.
                    public_identifier = (
                        not force_secret
                        and _is_public_scalar(item)
                        and _is_public_identifier_field(key)
                    )
                    if sensitive_field and not sensitive_container:
                        sanitized[safe_key] = REDACTED
                        continue
                    sanitized[safe_key] = _redact_value(
                        item,
                        redact_unlabeled=(
                            False if public_identifier else redact_unlabeled
                        ),
                        force_secret=force_secret or sensitive_container,
                        depth=depth + 1,
                        state=state,
                    )
            except Exception:
                sanitized["__redaction_error__"] = "[UNAVAILABLE]"
            return sanitized

        if isinstance(value, (list, tuple, set, frozenset)):
            sanitized_items = []
            try:
                for index, item in enumerate(value):
                    if index >= _MAX_CONTAINER_ITEMS:
                        sanitized_items.append(_TRUNCATED)
                        break
                    sanitized_items.append(
                        _redact_value(
                            item,
                            redact_unlabeled=redact_unlabeled,
                            force_secret=force_secret,
                            depth=depth + 1,
                            state=state,
                        )
                    )
            except Exception:
                sanitized_items.append("[UNAVAILABLE]")
            if isinstance(value, tuple):
                return tuple(sanitized_items)
            # Sets become JSON-safe lists; repr/set rendering is never allowed
            # to bypass recursive sanitization.
            return sanitized_items

        attributes = _object_attributes(value)
        if attributes is not None:
            sanitized_attributes = _redact_value(
                attributes,
                redact_unlabeled=redact_unlabeled,
                force_secret=force_secret or _is_sensitive_object(value),
                depth=depth + 1,
                state=state,
            )
            if isinstance(sanitized_attributes, dict):
                return {
                    "__type__": type(value).__name__,
                    **sanitized_attributes,
                }
            return sanitized_attributes

        # Never call an opaque object's __str__/__repr__: either may contain a
        # credential that has no detectable label (for example "tiny-secret").
        return f"<{type(value).__name__}>"
    finally:
        state.active_ids.remove(tracked_id)


def redact_value(value: Any, *, redact_unlabeled: bool = True) -> Any:
    """Recursively produce a cycle/depth/size-safe sanitized representation."""
    return _redact_value(
        value,
        redact_unlabeled=redact_unlabeled,
        force_secret=False,
        depth=0,
        state=_RedactionState(active_ids=set()),
    )
