"""Callable schemas register with resolvable, collision-safe root definitions."""

from __future__ import annotations

from typing import Any, Protocol, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError
from pydantic import BaseModel, Field, create_model

from maivn import Agent, depends_on_tool, toolify, toolset


class _Validator(Protocol):
    def validate(self, instance: object) -> None:
        """Validate a payload with the installed JSON Schema implementation."""


def _validator(schema: dict[str, Any]) -> _Validator:
    return cast('_Validator', Draft202012Validator(schema))


class _Cell(BaseModel):
    text: str = Field(min_length=1)


class _Report(BaseModel):
    cells: list[_Cell]


class _Node(BaseModel):
    label: str
    children: list[_Node] = Field(default_factory=list)


def test_injected_model_definitions_are_removed_unless_still_referenced() -> None:
    """Dependency-owned fields must not repeatedly publish dead nested schemas."""

    class Final(BaseModel):
        report: _Report
        tree: _Node
        cells: list[_Cell]

    depends_on_tool('build_report', 'report')(Final)
    agent = Agent(name='schema-pruning', api_key='test-key', base_url='http://testserver')
    agent.toolify()(Final)
    schema = next(tool.input_schema for tool in agent.list_tools() if tool.name == 'Final')
    assert '_Report' not in schema['$defs']
    assert '_Cell' in schema['$defs']
    assert '_Node' in schema['$defs']
    validator = _validator(schema)
    validator.validate(
        {'tree': {'label': 'root', 'children': [{'label': 'leaf'}]}, 'cells': [{'text': 'visible'}]}
    )
    with pytest.raises(ValidationError):
        validator.validate({'tree': {'label': 'root'}, 'cells': [{'text': ''}]})
    assert 'report' in Final.model_json_schema()['properties']


def test_public_agent_callable_nested_model_schema_validates_real_payloads() -> None:
    """Nested parameter refs resolve from the published function-schema root."""
    agent = Agent(name='schema-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify()
    def render(report: _Report, title: str, copies: int = 1) -> str:
        return f'{title}:{copies}:{len(report.cells)}'

    _ = render
    for tool in (agent.list_tools()[0], agent.compile_tools()[0]):
        validator = _validator(tool.input_schema)
        validator.validate({'report': {'cells': [{'text': 'Ready'}]}, 'title': 'Report'})
        with pytest.raises(ValidationError):
            validator.validate({'report': {'cells': [{'text': ''}]}, 'title': 'Report'})
        with pytest.raises(ValidationError):
            validator.validate({'report': {'cells': []}})
        assert tool.input_schema['properties']['title'] == {'type': 'string'}
        assert tool.input_schema['properties']['copies'] == {'type': 'integer'}


def test_public_agent_callable_recursive_schema_keeps_backreferences() -> None:
    """A recursive input remains recursive after embedding under callable properties."""
    agent = Agent(name='tree-agent', api_key='test-key', base_url='http://testserver')

    @agent.toolify()
    def visit(node: _Node) -> str:
        return node.label

    _ = visit
    validator = _validator(agent.compile_tools()[0].input_schema)
    validator.validate(
        {
            'node': {
                'label': 'root',
                'children': [
                    {'label': 'child', 'children': [{'label': 'leaf'}]},
                ],
            }
        }
    )
    with pytest.raises(ValidationError):
        validator.validate({'node': {'label': 'root', 'children': [{'label': 3}]}})


def test_public_agent_callable_conflicting_model_names_keep_distinct_meanings() -> None:
    """Same-named models from different parameters cannot overwrite each other's constraints."""
    text_choice = create_model('Choice', text=(str, ...), __module__='shared_models')
    number_choice = create_model('Choice', count=(int, ...), __module__='shared_models')
    text_payload = create_model('Payload', choice=(text_choice, ...), __module__='shared_models')
    number_payload = create_model(
        'Payload', choice=(number_choice, ...), __module__='shared_models'
    )

    def compare(left: object, right: object) -> str:
        return f'{left}:{right}'

    compare.__annotations__ = {'left': text_payload, 'right': number_payload, 'return': str}
    agent = Agent(name='collision-agent', api_key='test-key', base_url='http://testserver')
    agent.add_tool(compare)
    validator = _validator(agent.compile_tools()[0].input_schema)
    validator.validate({'left': {'choice': {'text': 'alpha'}}, 'right': {'choice': {'count': 2}}})
    invalid_inputs: list[dict[str, Any]] = [
        {'left': {'choice': {'count': 2}}, 'right': {'choice': {'count': 2}}},
        {'left': {'choice': {'text': 'alpha'}}, 'right': {'choice': {'text': 'alpha'}}},
    ]
    for invalid in invalid_inputs:
        with pytest.raises(ValidationError):
            validator.validate(invalid)


def test_public_toolset_registration_and_recompilation_preserve_definition_links() -> None:
    """Bound toolset methods need no post-registration cache mutation to validate inputs."""

    @toolset(prefix='reports')
    class Reports:
        @toolify()
        def render(self, report: _Report) -> int:
            return len(report.cells)

    agent = Agent(name='toolset-schema-agent', api_key='test-key', base_url='http://testserver')
    registered = agent.add_toolset(Reports())
    for tool in (*registered, *agent.compile_tools()):
        validator = _validator(tool.input_schema)
        validator.validate({'report': {'cells': [{'text': 'First row'}]}})
        with pytest.raises(ValidationError):
            validator.validate({'report': {'cells': [{'text': False}]}})
