"""Characterize the invocation-local placeholders that hide editable-source values."""

from __future__ import annotations

import re
from typing import cast

import pytest

from maivn._internal.private_workspace import PrivateWorkspaceValues, workspace_scope

_TOKEN = re.compile(r'\{_\{user_sdk_source_[0-9a-f]{32}\}_\}')
_HEX_ID = '0123456789abcdef0123456789abcdef'
_UUID_ID = '01234567-89ab-cdef-0123-456789abcdef'


def _is_token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast('dict[str, object]', value)


def _items(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast('list[object]', value)


def test_shield_hides_text_but_keeps_structure_enums_and_opaque_ids() -> None:
    """Labels and free text become tokens; closed enums and opaque ids stay readable."""
    values = PrivateWorkspaceValues()
    shielded = values.shield(
        {
            'kind': 'table',
            'title': 'Quarterly revenue',
            'document_id': _HEX_ID,
            'image_id': _UUID_ID,
            'alignment': 'center',
            'count': 3,
            'ratio': 0.5,
            'visible': True,
            'missing': None,
        }
    )
    shielded = _mapping(shielded)
    assert shielded['kind'] == 'table'
    assert shielded['document_id'] == _HEX_ID
    assert shielded['image_id'] == _UUID_ID
    assert shielded['alignment'] == 'center'
    assert shielded['count'] == 3  # noqa: PLR2004 - structural numbers outside content stay.
    assert shielded['ratio'] == 0.5  # noqa: PLR2004 - structural numbers outside content stay.
    assert shielded['visible'] is True
    assert shielded['missing'] is None
    assert _is_token(shielded['title'])


def test_enum_words_and_ids_are_hidden_under_any_other_key() -> None:
    """An enum word or id-shaped value is only trusted under the key that defines it."""
    values = PrivateWorkspaceValues()
    shielded = values.shield(
        {
            'title': 'table',
            'kind': 'Table',
            'workbook_id': 'Q3 forecast.xlsx',
            'presentation_id': _HEX_ID.upper(),
            'label': _HEX_ID,
            'image_id': f'{_HEX_ID}.png',
        }
    )
    assert len(_mapping(shielded)) == 6  # noqa: PLR2004 - every key survives.
    assert all(_is_token(item) for item in _mapping(shielded).values())


def test_content_numbers_and_booleans_are_hidden_recursively() -> None:
    """Every scalar under a content key, at any depth, becomes a token."""
    values = PrivateWorkspaceValues()
    shielded = values.shield({'content': {'cells': [[7, 2.5], [False, 'x']]}, 'rows': [7]})
    content = _mapping(_mapping(shielded)['content'])
    flat = [item for row in _items(content['cells']) for item in _items(row)]
    assert len(flat) == 4  # noqa: PLR2004 - two rows of two cells.
    assert all(_is_token(item) for item in flat)
    assert _mapping(shielded)['rows'] == [7]


def test_list_items_keep_the_parent_key_for_enum_checks() -> None:
    """Enum words inside a list are judged against the list's own key."""
    values = PrivateWorkspaceValues()
    shielded = values.shield({'style': ['accent', 'loud']})
    styles = _items(_mapping(shielded)['style'])
    assert styles[0] == 'accent'
    assert _is_token(styles[1])


def test_mapping_keys_are_stringified() -> None:
    """Non-string mapping keys are emitted as their string form."""
    values = PrivateWorkspaceValues()
    shielded = values.shield({1: 'one'})
    assert list(_mapping(shielded)) == ['1']


def test_tokens_are_stable_per_value_and_distinct_per_type() -> None:
    """Equal values of different types never share a token or restore as each other."""
    values = PrivateWorkspaceValues()
    shielded = values.shield({'content': [1, True, 1.0, 1, 'same', 'same']})
    tokens = _items(_mapping(shielded)['content'])
    assert tokens[0] == tokens[3]
    assert tokens[4] == tokens[5]
    assert len({tokens[0], tokens[1], tokens[2], tokens[4]}) == 4  # noqa: PLR2004 - four identities.
    restored = values.resolve(tokens)
    assert restored == [1, True, 1.0, 1, 'same', 'same']
    assert [type(item) for item in _items(restored)] == [int, bool, float, int, str, str]


def test_resolve_restores_exact_values_and_embedded_text() -> None:
    """A whole token restores its typed value; an embedded token restores its text form."""
    values = PrivateWorkspaceValues()
    shielded = values.shield({'content': {'total': 42, 'name': 'Ada'}})
    content = _mapping(_mapping(shielded)['content'])
    total, name = content['total'], content['name']
    assert isinstance(total, str)
    assert isinstance(name, str)
    resolved = values.resolve(
        {'cell': total, 'caption': f'{name} owes {total}', 'rows': [name, None, 5]}
    )
    assert resolved == {'cell': 42, 'caption': 'Ada owes 42', 'rows': ['Ada', None, 5]}


def test_resolve_leaves_unrelated_text_untouched() -> None:
    """Strings without this runtime's token syntax are returned unchanged."""
    values = PrivateWorkspaceValues()
    assert values.resolve('{_{pii_email_1}_} and plain text') == '{_{pii_email_1}_} and plain text'


@pytest.mark.parametrize(
    'value',
    [
        '{_{user_sdk_source_' + 'f' * 32 + '}_}',
        'prefix {_{sdk_source_unknown}_} suffix',
    ],
)
def test_resolve_refuses_tokens_this_invocation_did_not_issue(value: str) -> None:
    """A forged or foreign selector is refused rather than echoed or guessed."""
    values = PrivateWorkspaceValues()
    with pytest.raises(
        ValueError, match=r'^Private source selector is not available in this invocation$'
    ):
        values.resolve(value)


def test_tokens_do_not_cross_invocations() -> None:
    """One invocation's token cannot be resolved by another invocation's values."""
    first = PrivateWorkspaceValues()
    token = first.shield('secret text')
    second = PrivateWorkspaceValues()
    with pytest.raises(ValueError, match='not available'):
        second.resolve(token)


def test_workspace_scope_prefers_handles_then_ids_then_nested_values() -> None:
    """The owning selector comes from a handle first, then an id, then any nested value."""
    other = 'fedcba9876543210fedcba9876543210'
    assert workspace_scope({'document_id': other, 'doc': {'document_id': _HEX_ID}}) == _HEX_ID
    assert workspace_scope({'nested': {'image_id': _UUID_ID}, 'workbook_id': other}) == other
    assert (
        workspace_scope(
            {'items': [{'presentation_id': other}], 'wrapper': {'x': {'image_id': _HEX_ID}}}
        )
        == _HEX_ID
    )


def test_workspace_scope_ignores_filenames_and_non_mappings() -> None:
    """Filename-like ids and non-mapping values never name a workspace."""
    assert workspace_scope({'document_id': 'report.docx', 'name': 'x'}) is None
    assert workspace_scope({'document_id': 5}) is None
    assert workspace_scope([{'document_id': _HEX_ID}]) is None
    assert workspace_scope('document') is None
