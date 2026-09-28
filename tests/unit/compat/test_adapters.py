"""Unit tests for v1 compatibility adapters."""

from __future__ import annotations

import builtins
from typing import Any, Literal, cast, get_type_hints

import pytest

from maivn import (
    AgentGenerated,
    ClientBuilder,
    ConfigurationBuilder,
    FollowupQuestion,
    MaivnConfiguration,
    ModelConfig,
    PermissionFlag,
    PermissionSet,
    PIIWhitelist,
    PIIWhitelistEntry,
    PrivateData,
    ProviderCapability,
    ProviderMetadata,
    RedactedMessage,
    compose_argument_policy,
    default_terminal_followup,
    default_terminal_interrupt,
    depends_on_interrupt,
    depends_on_tool,
    get_configuration,
    require_permissions,
    requires_producer_reference,
    toolify,
    toolset,
)
from maivn._internal.compat.decorators import (
    ARGUMENT_PRODUCER_REQUIREMENTS_ATTR,
    COMPOSE_ARGUMENT_POLICIES_ATTR,
    INTERRUPT_DEPENDENCIES_ATTR,
    TOOL_DEPENDENCIES_ATTR,
    TOOLIFY_ATTR,
    TOOLSET_ATTR,
    ArgumentProducerRequirement,
    ComposeArgumentPolicy,
    InterruptDependency,
    ToolDependency,
)


def test_dependency_decorators_attach_v2_adapter_metadata() -> None:
    """Dependency decorators record metadata consumed by v2 tool registration."""

    def source() -> str:
        return 'context'

    @depends_on_tool(source, 'context')
    def target(context: str) -> str:
        return context

    dependencies = cast('list[ToolDependency]', getattr(target, TOOL_DEPENDENCIES_ATTR))

    assert len(dependencies) == 1
    assert dependencies[0].arg_name == 'context'
    assert dependencies[0].tool_name == 'source'


def test_argument_producer_decorator_attaches_typed_nested_requirement() -> None:
    """Reference requirements remain distinct from whole-result dependencies."""

    @requires_producer_reference(
        'block.composition',
        producer_tool_name='DOCUMENTS_compose_artifact',
    )
    def put_block(block: dict[str, object]) -> dict[str, object]:
        return block

    requirements = cast(
        'list[ArgumentProducerRequirement]',
        getattr(put_block, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR),
    )

    assert requirements == [
        ArgumentProducerRequirement(
            argument_path='block.composition',
            producer_tool_name='DOCUMENTS_compose_artifact',
            reference_kind='composition_reference',
        )
    ]
    assert not hasattr(put_block, TOOL_DEPENDENCIES_ATTR)


def test_compose_argument_policy_attaches_policy_and_required_reference() -> None:
    """Required argument composition records both policy and producer provenance."""

    @compose_argument_policy('query', mode='require', approval='explicit')
    def validate_query(query: str) -> str:
        return query

    policies = cast(
        'list[ComposeArgumentPolicy]',
        getattr(validate_query, COMPOSE_ARGUMENT_POLICIES_ATTR),
    )
    requirements = cast(
        'list[ArgumentProducerRequirement]',
        getattr(validate_query, ARGUMENT_PRODUCER_REQUIREMENTS_ATTR),
    )

    assert policies == [ComposeArgumentPolicy('query', 'require', 'explicit')]
    assert requirements == [
        ArgumentProducerRequirement('query', 'compose_argument', 'composition_reference')
    ]


def test_interrupt_decorator_infers_literal_choices() -> None:
    """depends_on_interrupt maps v1 user input requests onto approval metadata."""

    def capture(value: str) -> str:
        return value

    @depends_on_interrupt('decision', capture, prompt='Approve?')
    def choose(decision: Literal['yes', 'no']) -> str:
        return decision

    dependencies = cast(
        'list[InterruptDependency]',
        getattr(choose, INTERRUPT_DEPENDENCIES_ATTR),
    )

    assert len(dependencies) == 1
    assert dependencies[0].input_type == 'choice'
    assert dependencies[0].choices == ('yes', 'no')


def test_agent_generated_prompt_is_a_distinct_string_singleton() -> None:
    """Only the exported sentinel, not an equal user string, requests generation."""

    def capture(value: str) -> str:
        return value

    @depends_on_interrupt('generated', capture, prompt=AgentGenerated)
    def generated(generated: str) -> str:
        return generated

    @depends_on_interrupt('literal', capture, prompt=str(AgentGenerated))
    def literal(literal: str) -> str:
        return literal

    generated_dependency = cast(
        'list[InterruptDependency]',
        getattr(generated, INTERRUPT_DEPENDENCIES_ATTR),
    )[0]
    literal_dependency = cast(
        'list[InterruptDependency]',
        getattr(literal, INTERRUPT_DEPENDENCIES_ATTR),
    )[0]

    assert isinstance(AgentGenerated, str)
    assert generated_dependency.prompt is AgentGenerated
    assert generated_dependency.prompt_source == 'agent_generated'
    assert literal_dependency.prompt == AgentGenerated
    assert literal_dependency.prompt is not AgentGenerated
    assert literal_dependency.prompt_source == 'authored'
    assert get_type_hints(depends_on_interrupt)['prompt'] is str
    assert get_type_hints(InterruptDependency)['prompt'] is str


def test_toolset_and_toolify_attach_registration_metadata() -> None:
    """toolset/toolify keep the class-instance authoring path declarative."""

    @toolset(prefix='github')
    class GitHubTools:
        @toolify(name='current_user', tags=['identity'])
        def get_user(self) -> str:
            return 'octocat'

    toolset_options = getattr(GitHubTools, TOOLSET_ATTR)
    toolify_options = getattr(GitHubTools.get_user, TOOLIFY_ATTR)

    assert toolset_options.prefix == 'github'
    assert toolify_options.name == 'current_user'
    assert toolify_options.tags == ('identity',)


def test_permission_set_and_require_permissions_preserve_v1_semantics() -> None:
    """Permission helpers keep bitflag composition and validation behavior."""
    granted = PermissionSet(PermissionFlag.READ | PermissionFlag.WRITE)

    require_permissions(granted, PermissionFlag.READ)

    assert granted.includes(PermissionFlag.WRITE)
    assert granted.to_list() == ['read', 'write']


def test_configuration_builder_delegates_to_v2_client_config_shape() -> None:
    """ConfigurationBuilder builds the compatibility config around v2 client settings."""
    config = ConfigurationBuilder.from_dict(
        {
            'server': {'base_url': 'https://api.example.test', 'timeout_seconds': 12},
            'security': {'api_key': 'test-key'},
        },
    )

    assert isinstance(config, MaivnConfiguration)
    assert config.server.base_url == 'https://api.example.test'
    assert config.to_client_config().api_key == 'test-key'


def test_client_builder_from_environment_reads_sdk_connection_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment-named builder must use the one documented connection variable."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env-data.example')

    client = ClientBuilder.from_environment()

    assert client.config.api_key == 'env-key'
    assert client.config.base_url_text == 'https://env-data.example'
    assert client.config.base_url_text == 'https://env-data.example'


def test_configuration_builder_from_environment_reads_sdk_connection_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented configuration builder exposes the one public endpoint and the key."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env-data.example')

    config = ConfigurationBuilder.from_environment()

    assert config.security.api_key == 'env-key'
    assert config.server.base_url == 'https://env-data.example'


def test_client_builder_explicit_connection_values_override_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit builder values take precedence over environment-backed values."""
    monkeypatch.setenv('MAIVN_API_KEY', 'env-key')
    monkeypatch.setenv('MAIVN_BASE_URL', 'https://env-data.example')

    client = ClientBuilder.from_environment(
        api_key='explicit-key',
        base_url='https://explicit-data.example',
    )

    assert client.config.api_key == 'explicit-key'
    assert client.config.base_url_text == 'https://explicit-data.example'
    assert client.config.base_url_text == 'https://explicit-data.example'


def test_get_configuration_returns_context_default() -> None:
    """get_configuration remains a no-argument v1 import with a typed result."""
    assert isinstance(get_configuration(), MaivnConfiguration)


def test_model_and_provider_adapters_keep_common_helpers() -> None:
    """Typed option adapters preserve common v1 helpers."""
    model_config = ModelConfig.for_all(tier='fast')
    metadata = ProviderMetadata(
        name='github',
        display_name='GitHub',
        version='1.0.0',
        capabilities=frozenset({ProviderCapability.READ}),
    )

    assert model_config.response is not None
    assert model_config.response.tier == 'fast'
    assert metadata.has_capability(ProviderCapability.READ)


def test_privacy_adapters_preserve_validation_and_terminal_interrupt() -> None:
    """Private data and redaction helpers are constructible without v1 imports."""
    entry = PIIWhitelistEntry(value='support@maivn.io', justification='Public support inbox')
    whitelist = PIIWhitelist(entries=(entry,))
    private_data = PrivateData(value='Maria Santos')

    assert not whitelist.is_empty()
    assert private_data.value == 'Maria Santos'
    assert callable(default_terminal_interrupt)


def test_default_terminal_interrupt_discards_one_leading_terminal_bom(
    monkeypatch: object,
) -> None:
    """A terminal encoding marker must not become part of the interrupt answer."""

    def fake_input(_prompt: str) -> str:
        return '\ufeffDEPLOY-290'

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)

    assert default_terminal_interrupt('Ticket: ') == 'DEPLOY-290'


@pytest.mark.parametrize(
    ('prompt', 'expected_prompt'),
    [
        ('', ''),
        ('What account ID should I use?', 'What account ID should I use? '),
        ('What account ID should I use? ', 'What account ID should I use? '),
        ('What account ID should I use?\n', 'What account ID should I use?\n'),
    ],
)
def test_default_terminal_interrupt_separates_answers_without_rewriting_existing_whitespace(
    monkeypatch: object,
    prompt: str,
    expected_prompt: str,
) -> None:
    """Questions without terminal whitespace receive exactly one visible separator."""
    prompts: list[str] = []

    def fake_input(input_prompt: str) -> str:
        prompts.append(input_prompt)
        return 'ACCT-290'

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)

    assert default_terminal_interrupt(prompt) == 'ACCT-290'
    assert prompts == [expected_prompt]


def test_default_terminal_followup_renders_options_and_free_text_escape(
    monkeypatch: object,
) -> None:
    """The shipped handler presents typed choices and always permits an Other answer."""
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return 'Other region'

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)
    question = FollowupQuestion.model_validate(
        {
            'checkpoint_id': 'followup-1',
            'thread_id': 'thread-1',
            'session_id': 'session-1',
            'header': 'Region',
            'question': 'Which region?',
            'options': [
                {'label': 'US East', 'description': 'Lowest latency.'},
            ],
            'multi_select': False,
            'required': True,
        },
    )

    answer = default_terminal_followup(question, {'type': 'string'})

    assert answer == 'Other region'
    assert 'US East — Lowest latency.' in prompts[0]
    assert 'Other' in prompts[0]


def test_default_terminal_followup_discards_terminal_bom_before_resolving_option(
    monkeypatch: object,
) -> None:
    """A leading terminal BOM cannot turn a numbered choice into free text."""

    def fake_input(_prompt: str) -> str:
        return '\ufeff2'

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)
    question = FollowupQuestion.model_validate(
        {
            'checkpoint_id': 'followup-bom',
            'thread_id': 'thread-1',
            'session_id': 'session-1',
            'question': 'Choose a region.',
            'options': [
                {'label': 'US East', 'description': 'Lowest latency.'},
                {'label': 'EU West', 'description': 'EU residency.'},
            ],
        },
    )

    assert default_terminal_followup(question, {'type': 'string'}) == 'EU West'


@pytest.mark.parametrize(
    ('raw', 'schema', 'multi_select', 'expected'),
    [
        ('yes', {'type': 'boolean'}, False, True),
        ('no', {'type': 'boolean'}, False, False),
        ('1', {'type': 'string'}, False, 'US East'),
        ('1,2', {'type': 'array', 'items': {'type': 'string'}}, True, ['US East', 'EU West']),
        ('{"region":"us-east-1"}', {'type': 'object'}, False, {'region': 'us-east-1'}),
    ],
)
def test_default_terminal_followup_returns_declared_typed_answer(
    monkeypatch: object,
    raw: str,
    schema: dict[str, object],
    multi_select: object,
    expected: object,
) -> None:
    """The shipped handler covers terminal parity for every canonical answer shape."""

    def fake_input(_prompt: str) -> str:
        return raw

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)
    question = FollowupQuestion.model_validate(
        {
            'checkpoint_id': 'followup-typed',
            'thread_id': 'thread-1',
            'session_id': 'session-1',
            'question': 'Choose.',
            'options': [
                {'label': 'US East', 'description': 'Lowest latency.'},
                {'label': 'EU West', 'description': 'EU residency.'},
            ],
            'multi_select': multi_select,
        },
    )

    assert default_terminal_followup(question, schema) == expected


@pytest.mark.parametrize('multiple', [False, True])
def test_terminal_choices_submit_declared_values_and_preserve_display_labels(
    monkeypatch: object,
    *,
    multiple: bool,
) -> None:
    """Numbered selection follows the declared domain, never a decorated label."""
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return '2,3' if multiple else '2'

    cast('Any', monkeypatch).setattr(builtins, 'input', fake_input)
    question = FollowupQuestion.model_validate(
        {
            'checkpoint_id': 'choice-values',
            'thread_id': 'thread-1',
            'session_id': 'session-1',
            'question': 'Choose.',
            'multi_select': not multiple,
            'options': [
                {'label': 'Europe', 'value': 'west', 'description': 'EU residency.'},
                {'label': 'East (fast)', 'description': 'Invalid association.'},
            ],
        }
    )
    scalar = {'type': 'string', 'enum': ['east', 'west', 'Other']}
    schema = {'type': 'array', 'items': scalar} if multiple else scalar
    assert default_terminal_followup(question, schema) == (
        ['west', 'Other'] if multiple else 'west'
    )
    assert '2. Europe — EU residency.' in prompts[0]
    assert 'East (fast)' not in prompts[0]
    assert '3. Other' in prompts[0]
    assert 'free-text answer' not in prompts[0]


def test_redacted_message_serializes_redaction_boundary_to_v2_contract() -> None:
    """The v1 adapter must not degrade a redacted message into a plain user message."""
    entry = PIIWhitelistEntry(value='support@maivn.io', justification='Public support inbox')
    message = RedactedMessage(
        content='Contact Maria at maria@example.com.',
        known_pii_values=[PrivateData(value='Maria', name='customer_name'), 'maria@example.com'],
        pii_whitelist=PIIWhitelist(entries=(entry,)),
    )

    contract = message.to_contract()

    assert contract.redaction is not None
    assert contract.redaction.model_dump(mode='json', exclude_none=True) == {
        'pii_whitelist': {
            'entries': [
                {
                    'value': 'support@maivn.io',
                    'justification': 'Public support inbox',
                }
            ],
            'phi_mode': False,
        },
        'known_pii_values': [
            {'value': 'Maria', 'name': 'customer_name'},
            'maria@example.com',
        ],
    }
