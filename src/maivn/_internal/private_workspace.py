"""Keep private editable-source values in the SDK while models use opaque selectors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast
from uuid import uuid4

_TOKEN = re.compile(r'\{_\{((?:user_)?sdk_source_[^{}]*)\}_\}')
_OPAQUE_ID = re.compile(r'[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_ENUMS = {
    'kind': {'text', 'list', 'table', 'chart', 'image', 'scalar', 'row', 'matrix', 'formulas'},
    'type': {
        'title',
        'heading',
        'paragraph',
        'bullet_list',
        'numbered_list',
        'table',
        'image',
        'page_break',
        'spacer',
        'text',
        'list',
        'chart',
        'bar',
        'line',
    },
    'anchor': {'start', 'end', 'before', 'after'},
    'alignment': {'left', 'center', 'right'},
    'orientation': {'portrait', 'landscape'},
    'mode': {'auto', 'fit_width', 'actual_size'},
    'style': {'default', 'accent', 'muted'},
}


class PrivateWorkspaceValues:
    """Invocation-local reversible placeholders; this mapping never enters tool outcomes."""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._keys: dict[tuple[type[object], object], str] = {}

    def _token(self, *, value: str | float | bool) -> str:
        identity = (type(value), value)
        key = self._keys.get(identity)
        if key is None:
            key = f'user_sdk_source_{uuid4().hex}'
            self._keys[identity] = key
            self._values[key] = value
        return '{_{' + key + '}_}'

    def shield(self, value: object, *, key: str = '', content: bool = False) -> object:
        """Preserve structure and opaque IDs while concealing source text, labels and cells."""
        if isinstance(value, Mapping):
            return {
                str(name): self.shield(item, key=str(name), content=content or name == 'content')
                for name, item in cast('Mapping[object, object]', value).items()
            }
        if isinstance(value, list):
            return [
                self.shield(item, key=key, content=content) for item in cast('list[object]', value)
            ]
        if isinstance(value, str):
            if value in _ENUMS.get(key, set()) or (
                key in {'document_id', 'presentation_id', 'workbook_id', 'image_id'}
                and _OPAQUE_ID.fullmatch(value)
            ):
                return value
            return self._token(value=value)
        if content and isinstance(value, (int, float, bool)):
            return self._token(value=value)
        return value

    def resolve(self, value: object) -> object:
        """Restore only this runtime's private source tokens, including typed cell values."""
        if isinstance(value, str):
            exact = _TOKEN.fullmatch(value)
            if exact is not None:
                return self._value(exact.group(1))
            return _TOKEN.sub(lambda match: str(self._value(match.group(1))), value)
        if isinstance(value, Mapping):
            return {
                key: self.resolve(item)
                for key, item in cast('Mapping[object, object]', value).items()
            }
        if isinstance(value, list):
            return [self.resolve(item) for item in cast('list[object]', value)]
        return value

    def _value(self, key: str) -> object:
        if key not in self._values:
            message = 'Private source selector is not available in this invocation'
            raise ValueError(message)
        return self._values[key]


def workspace_scope(value: object) -> str | None:
    """Find the owning workspace selector without treating filenames as identity."""
    if isinstance(value, Mapping):
        mapping = cast('Mapping[object, object]', value)
        for handle in ('doc', 'document', 'workbook', 'presentation', 'image'):
            selected = workspace_scope(mapping.get(handle))
            if selected is not None:
                return selected
        for key in ('document_id', 'presentation_id', 'workbook_id', 'image_id'):
            selected = mapping.get(key)
            if isinstance(selected, str) and _OPAQUE_ID.fullmatch(selected):
                return selected
        for item in mapping.values():
            selected = workspace_scope(item)
            if selected is not None:
                return selected
    return None
