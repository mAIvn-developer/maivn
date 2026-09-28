"""Exact local substitutions retain display provenance without value matching."""

from maivn._internal.private_placeholders import resolve_private_placeholders_with_restorations


def test_nested_restorations_use_code_points_and_escaped_json_pointers() -> None:
    """Emoji and Markdown in values are literal data with exact, repeated spans."""
    source = {'a/b~c': ['😀 {_{email}_} then {_{email}_} and literal private@example.com']}
    restored, spans = resolve_private_placeholders_with_restorations(
        source,
        {'email': '**私**'},
    )
    assert restored == {'a/b~c': ['😀 **私** then **私** and literal private@example.com']}
    assert spans == [
        {'path': '/a~1b~0c/0', 'start': 2, 'end': 7, 'key': 'email'},
        {'path': '/a~1b~0c/0', 'start': 13, 'end': 18, 'key': 'email'},
    ]
    assert source['a/b~c'][0].startswith('😀 {_{email}_}')


def test_unresolved_and_already_literal_values_do_not_gain_provenance() -> None:
    """Only a local placeholder substitution proves a restored private value."""
    source = 'private@example.com {_{unknown}_}'
    restored, spans = resolve_private_placeholders_with_restorations(
        source,
        {'email': 'private@example.com'},
    )
    assert restored == source
    assert spans == []


def test_caller_aliases_and_scalar_values_preserve_existing_resolution() -> None:
    """Producer annotations use the observed protected key and actual scalar text."""
    restored, spans = resolve_private_placeholders_with_restorations(
        '{_{user_claim_id}_}:{_{count}_}',
        {'Claim ID': 'A', 'count': 0},
    )
    assert restored == 'A:0'
    assert spans == [
        {'path': '', 'start': 0, 'end': 1, 'key': 'user_claim_id'},
        {'path': '', 'start': 2, 'end': 3, 'key': 'count'},
    ]
