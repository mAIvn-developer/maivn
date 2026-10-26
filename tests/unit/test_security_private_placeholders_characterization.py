"""Characterize local private-placeholder resolution and restoration provenance."""

from __future__ import annotations

from typing import cast

from maivn._internal.private_placeholders import (
    project_private_restorations,
    rebase_private_restorations,
    resolve_private_placeholders,
    resolve_private_placeholders_with_restorations,
)


def test_empty_private_data_returns_the_value_itself() -> None:
    """Without private data nothing is copied or substituted."""
    value = {'text': '{_{email}_}'}
    assert resolve_private_placeholders(value, {}) is value


def test_placeholders_resolve_through_mappings_and_lists_only() -> None:
    """Strings inside mappings and lists resolve; keys and tuples are left alone."""
    resolved = resolve_private_placeholders(
        {'{_{k}_}': ['{_{k}_} and {_{n}_}', ('{_{k}_}',), 5]},
        {'k': 'value', 'n': 7},
    )
    assert resolved == {'{_{k}_}': ['value and 7', ('{_{k}_}',), 5]}


def test_unknown_and_none_valued_keys_stay_visible() -> None:
    """An unresolvable placeholder is left intact rather than blanked."""
    assert (
        resolve_private_placeholders(
            '{_{gone}_}|{_{empty}_}|{_{none}_}', {'empty': '', 'none': None}
        )
        == '{_{gone}_}||{_{none}_}'
    )


def test_exact_caller_keys_win_over_derived_ones() -> None:
    """An exact key beats the unprefixed name, which beats the sanitized spelling."""
    assert (
        resolve_private_placeholders(
            '{_{user_claim_id}_}', {'Claim ID': 'sanitized', 'user_claim_id': 'exact'}
        )
        == 'exact'
    )
    assert (
        resolve_private_placeholders(
            '{_{user_claim_id}_}', {'Claim ID': 'sanitized', 'claim_id': 'unprefixed'}
        )
        == 'unprefixed'
    )
    assert resolve_private_placeholders('{_{user_claim_id}_}', {'Claim ID': 'B'}) == 'B'


def test_first_sanitized_spelling_wins() -> None:
    """When two caller keys sanitize alike the first supplied one is used."""
    assert (
        resolve_private_placeholders(
            '{_{user_claim_id}_}', {'Claim ID': 'first', 'claim-id': 'second'}
        )
        == 'first'
    )


def test_positional_server_keys_are_never_guessed() -> None:
    """A server-allocated positional key resolves only from an exact caller entry."""
    assert resolve_private_placeholders('{_{pii_person_2}_}', {'person': 'Ada'}) == (
        '{_{pii_person_2}_}'
    )


def test_restorations_skip_empty_replacements_and_record_paths() -> None:
    """An empty restored value produces no provenance span; others are exact."""
    restored, spans = resolve_private_placeholders_with_restorations(
        {'rows': [{'cell': 'x{_{blank}_}y{_{name}_}'}], 'n': 3},
        {'blank': '', 'name': 'Ada'},
    )
    assert restored == {'rows': [{'cell': 'xyAda'}], 'n': 3}
    assert spans == [{'path': '/rows/0/cell', 'start': 2, 'end': 5, 'key': 'name'}]


def test_projection_rebases_spans_inside_the_window() -> None:
    """Only well-formed spans for the source path, fully inside the slice, are moved."""
    spans: list[object] = [
        {'path': '/a', 'start': 2, 'end': 4, 'key': 'k1'},
        {'path': '/a', 'start': 1, 'end': 3, 'key': 'before-window'},
        {'path': '/a', 'start': 4, 'end': 7, 'key': 'after-window'},
        {'path': '/a', 'start': 3, 'end': 3, 'key': 'empty'},
        {'path': '/b', 'start': 2, 'end': 4, 'key': 'other-path'},
        {'path': '/a', 'start': True, 'end': 4, 'key': 'bool-start'},
        {'path': '/a', 'start': 2, 'end': 4.0, 'key': 'float-end'},
        {'path': '/a', 'start': 2, 'end': 4, 'key': ''},
        {'path': '/a', 'start': 2, 'end': 4, 'key': 5},
        'not a span',
    ]
    assert project_private_restorations(spans, '/a', '/z', offset=2, length=4) == [
        {'path': '/z', 'start': 0, 'end': 2, 'key': 'k1'},
    ]
    assert project_private_restorations(spans, '/a', '/a', offset=1, length=5) == [
        {'path': '/a', 'start': 1, 'end': 3, 'key': 'k1'},
        {'path': '/a', 'start': 0, 'end': 2, 'key': 'before-window'},
    ]
    assert project_private_restorations({'path': '/a'}, '/a', '/a', length=9) == []


def test_rebase_shifts_server_spans_past_local_substitutions() -> None:
    """A server span moves by the length change of local substitutions before it."""
    payload: dict[str, object] = {
        'message': {'parts': ['{_{a}_} and NAME']},
        'private_value_restorations': [
            {'path': '/message/parts/0', 'start': 12, 'end': 16, 'key': 'server_key'},
        ],
    }
    assert rebase_private_restorations(payload, {'a': 'Alice'}) == [
        {'path': '/message/parts/0', 'start': 10, 'end': 14, 'key': 'server_key'},
    ]


def test_rebase_follows_escaped_json_pointers() -> None:
    """Pointer segments unescape ~1 and ~0 before lookup."""
    payload: dict[str, object] = {
        'a/b~c': 'NAME',
        'private_value_restorations': [{'path': '/a~1b~0c', 'start': 0, 'end': 4, 'key': 'k'}],
    }
    assert rebase_private_restorations(payload, {}) == [
        {'path': '/a~1b~0c', 'start': 0, 'end': 4, 'key': 'k'},
    ]


def test_rebase_drops_spans_that_do_not_address_a_string() -> None:
    """Malformed, relative, out-of-range or non-string targets yield no span."""
    payload: dict[str, object] = {
        'list': ['NAME'],
        'number': 5,
        'text': 'NAME',
        'private_value_restorations': [
            'not a span',
            {'path': 7, 'start': 0, 'end': 4, 'key': 'k'},
            {'path': 'xtext', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/text/0', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/list/1', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/list/first', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/number/0', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/number', 'start': 0, 'end': 4, 'key': 'k'},
            {'path': '/text', 'start': 0, 'end': 5, 'key': 'k'},
            {'path': '/list/0', 'start': 0, 'end': 4, 'key': 'kept'},
        ],
    }
    assert rebase_private_restorations(payload, {}) == [
        {'path': '/list/0', 'start': 0, 'end': 4, 'key': 'kept'},
    ]
    assert rebase_private_restorations({'private_value_restorations': 'x'}, {}) == []
    assert rebase_private_restorations(cast('dict[str, object]', {}), {}) == []
