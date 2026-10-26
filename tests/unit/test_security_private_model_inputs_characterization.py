"""Characterize caller-owned private fields injected at Pydantic's validation boundary."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, ValidationError

from maivn import depends_on_private_data
from maivn._internal.private_model_inputs import (
    PrivateInputJsonSchema,
    install_private_model_validation,
    validate_private_models,
)

_SENTINEL = 'sentinel-private-ssn-value'


@depends_on_private_data('ssn_key', 'ssn')
class Applicant(BaseModel):
    """A model whose ssn field is caller-owned private data."""

    name: str
    ssn: str


@depends_on_private_data('token_key', 'token')
@depends_on_private_data('ssn_key', 'ssn')
class DoublyPrivate(BaseModel):
    """A model with two private fields, decorated twice."""

    name: str
    ssn: str
    token: str


@depends_on_private_data('ssn_key', 'ssn')
class AliasedApplicant(BaseModel):
    """A private field reachable through a plain validation alias; extras are kept."""

    model_config = ConfigDict(extra='allow')

    name: str
    ssn: str = Field(validation_alias='SSN')


@depends_on_private_data('ssn_key', 'ssn')
class ChoiceApplicant(BaseModel):
    """A private field with several accepted spellings; extras are kept."""

    model_config = ConfigDict(extra='allow')

    ssn: str = Field(validation_alias=AliasChoices('primary_ssn', 'legacy_ssn'))


@depends_on_private_data('ssn_key', 'ssn')
class NestedApplicant(BaseModel):
    """A private field at a nested alias path."""

    ssn: str = Field(validation_alias=AliasPath('identity', 'numbers', 2))


@depends_on_private_data('ssn_key', 'ssn')
class TailApplicant(BaseModel):
    """A private field at a negative list position."""

    ssn: str = Field(validation_alias=AliasPath('numbers', -2))


@depends_on_private_data('ssn_key', 'ssn')
class SiblingRoot(BaseModel):
    """A private nested path whose root shares its name with an ordinary field."""

    ssn: str = Field(validation_alias=AliasPath('identity', 'ssn'))
    identity: dict[str, str] = Field(default_factory=dict[str, str])


class Plain(BaseModel):
    """An ordinary model without private fields."""

    name: str


class Opaque:
    """A developer object Pydantic cannot build a schema for."""


def test_private_value_is_injected_from_caller_data() -> None:
    """The caller's private value fills the field during an SDK call."""
    result = validate_private_models(Applicant, {'name': 'Ada'}, {'ssn_key': _SENTINEL})
    assert result == Applicant(name='Ada', ssn=_SENTINEL)


def test_model_supplied_value_cannot_override_the_private_value() -> None:
    """A value the model authored for a private field is replaced, never trusted."""
    result = validate_private_models(
        Applicant, {'name': 'Ada', 'ssn': 'forged'}, {'ssn_key': _SENTINEL}
    )
    assert result == Applicant(name='Ada', ssn=_SENTINEL)


def test_model_instances_are_copied_with_the_private_value() -> None:
    """An already-built model is copied with the private field replaced."""
    original = Applicant(name='Ada', ssn='forged')
    result = validate_private_models(Applicant, original, {'ssn_key': _SENTINEL})
    assert result == Applicant(name='Ada', ssn=_SENTINEL)
    assert original.ssn == 'forged'


def test_missing_private_key_names_only_the_key() -> None:
    """A missing private key fails validation naming the key, not any value."""
    with pytest.raises(ValidationError) as caught:
        validate_private_models(Applicant, {'name': 'Ada', 'ssn': _SENTINEL}, {'other': 'x'})
    errors = caught.value.errors()
    assert [(error['type'], error['msg']) for error in errors] == [
        ('private_data_missing', 'private data is missing key ssn_key'),
    ]


def test_every_dependency_of_a_twice_decorated_model_is_injected() -> None:
    """Decorating twice installs one hook that injects every declared field."""
    result = validate_private_models(
        DoublyPrivate,
        {'name': 'Ada', 'token': 'forged'},
        {'ssn_key': 'S', 'token_key': 'T'},
    )
    assert result == DoublyPrivate.model_validate({'name': 'Ada', 'ssn': 'S', 'token': 'T'})


def test_validation_without_sdk_context_does_not_inject() -> None:
    """Ordinary Pydantic validation outside an SDK call leaves supplied values alone."""
    assert Applicant.model_validate({'name': 'Ada', 'ssn': 'x'}).ssn == 'x'
    foreign = Applicant.model_validate({'name': 'Ada', 'ssn': 'x'}, context={'ssn_key': 'y'})
    assert foreign.ssn == 'x'


def test_alias_spellings_cannot_shadow_the_private_value() -> None:
    """Every single-segment alias spelling is removed before the value is placed."""
    aliased = validate_private_models(
        AliasedApplicant, {'name': 'Ada', 'SSN': 'forged', 'ssn': 'forged'}, {'ssn_key': 'S'}
    )
    assert isinstance(aliased, AliasedApplicant)
    assert aliased.ssn == 'S'
    assert aliased.model_extra == {}
    chosen = validate_private_models(
        ChoiceApplicant, {'legacy_ssn': 'forged', 'primary_ssn': 'forged'}, {'ssn_key': 'S'}
    )
    assert isinstance(chosen, ChoiceApplicant)
    assert chosen.ssn == 'S'
    assert chosen.model_extra == {}


def test_nested_alias_paths_are_built_without_touching_siblings() -> None:
    """A nested alias path is created or padded while unrelated values survive."""
    nested = validate_private_models(
        NestedApplicant, {'identity': {'numbers': ['a'], 'other': 1}}, {'ssn_key': 'S'}
    )
    assert isinstance(nested, NestedApplicant)
    assert nested.ssn == 'S'
    tail = validate_private_models(TailApplicant, {'numbers': 'not-a-list'}, {'ssn_key': 'T'})
    assert isinstance(tail, TailApplicant)
    assert tail.ssn == 'T'


def test_non_mapping_input_is_left_for_pydantic_to_reject() -> None:
    """A value that is neither a mapping nor the model is not injected into."""
    with pytest.raises(ValidationError) as caught:
        validate_private_models(Applicant, 'not a mapping', {'ssn_key': _SENTINEL})
    assert [error['type'] for error in caught.value.errors()] == ['model_type']
    assert _SENTINEL not in str(caught.value)


def test_private_models_inside_containers_receive_context() -> None:
    """Private models nested in other annotations are validated with caller data."""
    result = validate_private_models(
        list[Applicant], [{'name': 'Ada'}, {'name': 'Grace'}], {'ssn_key': 'S'}
    )
    assert result == [Applicant(name='Ada', ssn='S'), Applicant(name='Grace', ssn='S')]


def test_annotations_without_private_models_are_handled_by_kind() -> None:
    """Plain models are validated, other annotations and opaque objects pass through."""
    assert validate_private_models(Plain, {'name': 'Ada'}, {}) == Plain(name='Ada')
    assert validate_private_models(int, 'not-validated', {}) == 'not-validated'
    marker = Opaque()
    assert validate_private_models(Opaque, marker, {}) is marker


def test_json_schema_hides_private_fields_and_their_aliases() -> None:
    """The model-facing schema lists neither the private field nor its alias spellings."""
    schema = Applicant.model_json_schema(schema_generator=PrivateInputJsonSchema)
    assert schema['properties'] == {'name': {'title': 'Name', 'type': 'string'}}
    assert schema['required'] == ['name']
    aliased = AliasedApplicant.model_json_schema(
        schema_generator=PrivateInputJsonSchema, mode='validation'
    )
    assert set(aliased['properties']) == {'name'}
    assert aliased['required'] == ['name']
    nested: dict[str, Any] = NestedApplicant.model_json_schema(
        schema_generator=PrivateInputJsonSchema, mode='validation'
    )
    assert nested['properties'] == {}
    assert 'required' not in nested or nested['required'] == []
    choices = ChoiceApplicant.model_json_schema(
        schema_generator=PrivateInputJsonSchema, mode='validation'
    )
    assert choices['properties'] == {}
    sibling = SiblingRoot.model_json_schema(
        schema_generator=PrivateInputJsonSchema, mode='validation'
    )
    assert set(sibling['properties']) == {'identity'}


def test_install_ignores_non_models() -> None:
    """Installing on something that is not a Pydantic model is a no-op."""

    def tool() -> None:
        """Do nothing."""

    install_private_model_validation(tool)
    install_private_model_validation(Plain(name='Ada'))
    assert not hasattr(tool, '__maivn_private_validation__')
