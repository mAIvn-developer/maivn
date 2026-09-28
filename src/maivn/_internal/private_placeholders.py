"""Resolve private-data placeholders inside the developer's own process.

The server never holds a private value. It substitutes the value with a
`{_{key}_}` token, reasons over the token, and hands any resulting tool call back
with the token still embedded. This process - the developer's own, which IS the
vault boundary - has to turn the token back into the value before the tool runs.

Nothing did. An MCP fetch tool was handed the literal string `{_{alpha_url}_}`
and rejected it as a malformed URL, which emptied every live feed in
financial-planner-mcp while the run still reported success.

Two call sites need this, which is why it lives here rather than in either one:

- `tool_runtime`, for arguments the MODEL authored;
- `scope`, for MCP `tool_defaults` / injected arguments, which are DEVELOPER
  configuration merged in at call time, downstream of the runtime entirely.

The placeholder syntax itself comes from `maivn_contracts`, shared with the brain
that emits it, so the two halves cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypedDict, cast

from maivn_contracts.runtime import (
    PLACEHOLDER_PATTERN,
    caller_named_key_candidates,
    normalize_placeholder_key,
    sanitize_private_key_name,
)

if TYPE_CHECKING:
    import re


class PrivateValueRestoration(TypedDict):
    """A local substitution, addressed by JSON Pointer and Unicode code points."""

    path: str
    start: int
    end: int
    key: str


def project_private_restorations(
    restorations: object,
    source_path: str,
    target_path: str,
    *,
    offset: int = 0,
    length: int,
) -> list[PrivateValueRestoration]:
    """Rebase exact spans when a known payload string is sliced or renamed."""
    if not isinstance(restorations, list):
        return []
    projected: list[PrivateValueRestoration] = []
    for item in cast('list[object]', restorations):
        if not isinstance(item, dict):
            continue
        span = cast('dict[str, object]', item)
        start, end, key = span.get('start'), span.get('end'), span.get('key')
        if (
            span.get('path') == source_path
            and type(start) is int
            and type(end) is int
            and isinstance(key, str)
            and key
            and offset <= start < end <= offset + length
        ):
            projected.append(
                {'path': target_path, 'start': start - offset, 'end': end - offset, 'key': key}
            )
    return projected


def resolve_private_placeholders_with_restorations(
    value: object,
    private_data: Mapping[str, object],
) -> tuple[object, list[PrivateValueRestoration]]:
    """Resolve local values while retaining exact display provenance.

    Never infer provenance from literal value matches. Offsets address the
    restored string, not its Markdown rendering, and never include the value
    again in metadata. Callers must keep this local metadata out of uploads.
    """
    spans: list[PrivateValueRestoration] = []
    index = _sanitized_key_index(private_data)

    def walk(item: object, path: str) -> object:
        if isinstance(item, str):
            parts: list[str] = []
            cursor = 0
            offset = 0
            for match in PLACEHOLDER_PATTERN.finditer(item):
                prefix = item[cursor : match.start()]
                parts.append(prefix)
                offset += len(prefix)
                key = normalize_placeholder_key(match.group(1))
                resolved = _lookup(key, private_data, index)
                replacement = match.group(0) if resolved is None else str(resolved)
                if resolved is not None and replacement:
                    spans.append(
                        {
                            'path': path,
                            'start': offset,
                            'end': offset + len(replacement),
                            'key': key,
                        }
                    )
                parts.append(replacement)
                offset += len(replacement)
                cursor = match.end()
            parts.append(item[cursor:])
            return ''.join(parts)
        if isinstance(item, Mapping):
            return {
                key: walk(child, f'{path}/{str(key).replace("~", "~0").replace("/", "~1")}')
                for key, child in cast('Mapping[object, object]', item).items()
            }
        if isinstance(item, list):
            return [
                walk(child, f'{path}/{position}')
                for position, child in enumerate(cast('list[object]', item))
            ]
        return item

    return walk(value, ''), spans


def rebase_private_restorations(
    payload: Mapping[str, object],
    private_data: Mapping[str, object],
) -> list[PrivateValueRestoration]:
    """Retain server substitution provenance across additional local substitutions."""
    spans = payload.get('private_value_restorations')
    if not isinstance(spans, list):
        return []
    rebased: list[PrivateValueRestoration] = []
    for item in cast('list[object]', spans):
        if not isinstance(item, dict):
            continue
        span = cast('dict[str, object]', item)
        path = span.get('path')
        if not isinstance(path, str) or not path.startswith('/'):
            continue
        source: object = payload
        for segment in path[1:].split('/'):
            key = segment.replace('~1', '/').replace('~0', '~')
            if isinstance(source, Mapping):
                source = cast('Mapping[object, object]', source).get(key)
            elif isinstance(source, list) and key.isdecimal():
                items = cast('list[object]', source)
                source = items[int(key)] if int(key) < len(items) else None
            else:
                source = None
                break
        if not isinstance(source, str):
            continue
        for valid in project_private_restorations([span], path, path, length=len(source)):
            before, _ = resolve_private_placeholders_with_restorations(
                source[: valid['start']],
                private_data,
            )
            through, _ = resolve_private_placeholders_with_restorations(
                source[: valid['end']],
                private_data,
            )
            rebased.append({**valid, 'start': len(str(before)), 'end': len(str(through))})
    return rebased


def resolve_private_placeholders(
    value: object,
    private_data: Mapping[str, object],
) -> object:
    """Substitute `{_{key}_}` tokens with the developer's real private values.

    Recurses through mappings and lists: a placeholder travels wherever a string
    can, not only at the top level of an argument dict.

    An unregistered key is deliberately left untouched. Substituting an empty
    string would convert a visible failure into a silent wrong answer, which is
    the exact failure mode this function exists to end.
    """
    if not private_data:
        return value
    if isinstance(value, str):
        sanitized_index = _sanitized_key_index(private_data)

        def _replace(match: re.Match[str]) -> str:
            key = normalize_placeholder_key(match.group(1))
            resolved = _lookup(key, private_data, sanitized_index)
            if resolved is None:
                return match.group(0)
            return str(resolved)

        return PLACEHOLDER_PATTERN.sub(_replace, value)
    if isinstance(value, Mapping):
        mapping = cast('Mapping[object, object]', value)
        return {
            key: resolve_private_placeholders(item, private_data) for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [
            resolve_private_placeholders(item, private_data) for item in cast('list[object]', value)
        ]
    return value


def _lookup(
    key: str,
    private_data: Mapping[str, object],
    sanitized_index: Mapping[str, object],
) -> object | None:
    """Return the value this key names, or None when this process cannot resolve it.

    A value the caller supplied under a NAME comes back named `user_<sanitized name>`,
    because the server prefixes caller-chosen names so they cannot squat an
    auto-allocated slot. The caller's own map is keyed on the name it chose, so an
    exact-match-only lookup handed a tool the literal `{_{user_claim_id}_}` for a value
    the caller had supplied all along as `claim_id`.

    Exact keys win, so a caller whose map genuinely contains `user_claim_id` still gets
    that entry rather than a derived one. A key with no caller-side counterpart at all -
    a positional `pii_person_2` or `known_pii_1`, allocated by discovery order inside one
    server run - stays unresolved on purpose: this process never supplied it and must not
    guess.
    """
    for candidate in caller_named_key_candidates(key):
        if candidate in private_data:
            return private_data[candidate]
    for candidate in caller_named_key_candidates(key):
        sanitized = sanitize_private_key_name(candidate)
        if sanitized in sanitized_index:
            return sanitized_index[sanitized]
    return None


def _sanitized_key_index(private_data: Mapping[str, object]) -> dict[str, object]:
    """Index the caller's own keys by their canonical spelling.

    A caller naming a value `Claim ID` gets the key `user_claim_id` back; matching on
    the sanitized spelling is what connects the two without the caller having to know
    the derivation.
    """
    index: dict[str, object] = {}
    for key, value in private_data.items():
        index.setdefault(sanitize_private_key_name(str(key)), value)
    return index


__all__ = ['resolve_private_placeholders']
