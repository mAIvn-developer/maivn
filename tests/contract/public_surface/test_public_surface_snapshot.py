"""The committed record of the SDK's public surface matches the SDK.

`surface.jsonl` holds one line per public module and per public name reachable from
`maivn` and its public submodules: the kind of object, call signatures (parameter
names, kinds and defaults), class ancestry, public members, enum members, dataclass
parameters and each pydantic model's validation and serialization JSON schemas.
`schema_defs.jsonl` holds every shared schema definition once, keyed by digest.
`consumer_private.jsonl` records the private SDK names that other packages in this
repository import or patch: those cannot move or change shape without breaking them.

Any change to these files is a change to what customers (or in-repo consumers) can
rely on. Regenerate them by running this file with MAIVN_WRITE_PUBLIC_SURFACE_SNAPSHOT=1
set, and review the diff.

A class's defining module is deliberately not recorded: moving an implementation
between private modules keeps every import path, signature and schema identical.
"""

from __future__ import annotations

import ast
import dataclasses
import enum
import hashlib
import importlib
import inspect
import json
import os
import pkgutil
import re
import types
import typing
from pathlib import Path
from typing import Any, cast

import pydantic

import maivn

HERE = Path(__file__).parent
SURFACE_PATH = HERE / 'surface.jsonl'
DEFS_PATH = HERE / 'schema_defs.jsonl'
CONSUMER_PATH = HERE / 'consumer_private.jsonl'
WRITE_ENV = 'MAIVN_WRITE_PUBLIC_SURFACE_SNAPSHOT'

PUBLIC_MODULES = (
    'maivn',
    'maivn.__version__',
    'maivn.artifact_images',
    'maivn.cli',
    'maivn.connection_events',
    'maivn.decisions',
    'maivn.dev_tunnel',
    'maivn.events',
    'maivn.events.fastapi',
    'maivn.files',
    'maivn.logging',
    'maivn.messages',
    'maivn.postgres',
    'maivn.telemetry',
    'maivn.telemetry.opentelemetry',
)

# Private SDK names imported or patched by other packages in this repository, found by
# scanning apps, services, libraries and scripts on 2026-10-10 (Alpha and the Anvil
# connector excluded). Each is `module:name`; a bare `module` is a module attribute
# holder whose listed names consumers patch or read.
CONSUMER_PRIVATE_NAMES = (
    'maivn._internal.artifact_image_context:InvocationArtifactImages',
    'maivn._internal.claiming:ClaimedWork',
    'maivn._internal.client:_hydrated_client_event',
    'maivn._internal.client:_stream_with_local_tools',
    'maivn._internal.compat.decorators:ARGUMENT_PRODUCER_REQUIREMENTS_ATTR',
    'maivn._internal.compat.decorators:INTERRUPT_DEPENDENCIES_ATTR',
    'maivn._internal.compat.decorators:InterruptDependency',
    'maivn._internal.compat.decorators:PRIVATE_DATA_DEPENDENCIES_ATTR',
    'maivn._internal.compat.decorators:PrivateDataDependency',
    'maivn._internal.compat.decorators:TOOL_DEPENDENCIES_ATTR',
    'maivn._internal.compat.decorators:ToolDependency',
    'maivn._internal.compat.invocation:coerce_structured_response',
    'maivn._internal.compat.invocation:response_from_stream_events',
    'maivn._internal.compat.invocation:run_batch',
    'maivn._internal.compat.options:MemoryConfig',
    'maivn._internal.compat.options:SessionOrchestrationConfig',
    'maivn._internal.compat.options:SystemToolsConfig',
    'maivn._internal.compat.privacy:PIIWhitelist',
    'maivn._internal.compat.privacy:RedactedMessage',
    'maivn._internal.compat.scheduling:AtSchedule',
    'maivn._internal.compat.scheduling:CronInvocationBuilder',
    'maivn._internal.compat.scheduling:CronSchedule',
    'maivn._internal.compat.scheduling:IntervalSchedule',
    'maivn._internal.compat.scheduling:JitterSpec',
    'maivn._internal.compat.scheduling:Retry',
    'maivn._internal.compat.scheduling:RunRecord',
    'maivn._internal.compat.scheduling:Schedule',
    'maivn._internal.compat.scheduling:ScheduledJob',
    'maivn._internal.compat.tooling:preserve_public_tool_dependency_guidance',
    'maivn._internal.config:ClientConfig',
    'maivn._internal.config:local_vault_from_environment',
    'maivn._internal.error_diagnostics:attach_error_diagnostics',
    'maivn._internal.error_diagnostics:diagnostic_facts',
    'maivn._internal.error_diagnostics:error_action_hint',
    'maivn._internal.error_diagnostics:safe_error_message',
    'maivn._internal.errors:ConfigurationError',
    'maivn._internal.errors:MaivnSDKError',
    'maivn._internal.ids:create_uuid',
    'maivn._internal.inspection:InspectionCall',
    'maivn._internal.inspection:InspectionSource',
    'maivn._internal.inspection:OfflineReviewComposition',
    'maivn._internal.inspection:_RUBRIC_VERSION',
    'maivn._internal.inspection:_SemanticObservation',
    'maivn._internal.inspection:_SemanticReview',
    'maivn._internal.inspection:_enroll_offline_reviewer',
    'maivn._internal.inspection:admitted_hints',
    'maivn._internal.inspection:capture_invoke_inspection',
    'maivn._internal.inspection:digest',
    'maivn._internal.inspection:freeze',
    'maivn._internal.inspection:inspection_call_seal',
    'maivn._internal.inspection:inspection_worker',
    'maivn._internal.inspection:presentation_owner',
    'maivn._internal.inspection:thaw',
    'maivn._internal.models:InvokeResponse',
    'maivn._internal.models:StreamEvent',
    'maivn._internal.models:ToolMetadata',
    'maivn._internal.plan_limits:PlanLimitExceededError',
    'maivn._internal.private_artifacts',
    # scripts/staging_vault_e2e.py reads these through the module object.
    'maivn._internal.private_artifacts:PrivateArtifactError',
    'maivn._internal.private_artifacts:PrivateArtifactResolveRequest',
    'maivn._internal.private_artifacts:SENDER_ASSERTION_HEADER',
    'maivn._internal.private_artifacts:SENDER_SIGNATURE_HEADER',
    'maivn._internal.private_artifacts:_DATA_RESOLVE_PATH',
    'maivn._internal.private_artifacts:_DATA_TICKET_PATH',
    'maivn._internal.private_artifacts:_deep_primitive',
    'maivn._internal.private_placeholders:project_private_restorations',
    'maivn._internal.reporting.app_event_payloads:build_final_payload',
    'maivn._internal.reporting.app_event_payloads:build_tool_event_payload',
    'maivn._internal.reporting.context:current_reporter',
    'maivn._internal.reporting.context:current_sdk_delivery_mode',
    'maivn._internal.reporting.stream_events:StreamEventProjector',
    'maivn._internal.reporting.terminal_reporter:BaseReporter',
    'maivn._internal.reporting.terminal_reporter:event_router',
    'maivn._internal.reporting.terminal_reporter.base.reporter:BaseReporter',
    'maivn._internal.reporting.terminal_reporter.event_router.reporter:EventRouterReporter',
    'maivn._internal.scope:agent',
    'maivn._internal.scope.agent:Agent',
    'maivn._internal.scope.options:messages_with_system_prompt',
    'maivn._internal.scope.tools:compile_scope_tools',
    'maivn._internal.serving:ServingSession',
    'maivn._internal.sse_event:SSEEvent',
    'maivn._internal.token_usage:TokenUsage',
    'maivn._internal.tool_runtime:LocalToolRuntime',
    'maivn._internal.transport.http:HttpJsonClient',
    'maivn._internal.wire:_agent_spec',
    'maivn._internal.wire:_function_tool_spec',
    'maivn.events._bridge:safe_json_dumps',
    'maivn.events._bridge.bridge:EventBridge',
    'maivn.events._forwarding.state:NormalizedEventForwardingState',
    'maivn.events._models:NormalizedStreamState',
    'maivn.events._normalize.stream:normalize_stream_event',
    'maivn.events._vocabulary:ASSIGNMENT_COMPLETED_EVENT_NAME',
    'maivn.events._vocabulary:ASSIGNMENT_RECEIVED_EVENT_NAME',
    'maivn.events._vocabulary:ENRICHMENT_EVENT_NAME',
    'maivn.events._vocabulary:ERROR_EVENT_NAME',
    'maivn.events._vocabulary:FINAL_EVENT_NAME',
    'maivn.events._vocabulary:HEARTBEAT_EVENT_NAME',
    'maivn.events._vocabulary:HOOK_FIRED_EVENT_NAME',
    'maivn.events._vocabulary:INTERRUPT_REQUEST_EVENT_NAME',
    'maivn.events._vocabulary:INTERRUPT_REQUIRED_EVENT_NAME',
    'maivn.events._vocabulary:MODEL_TOOL_COMPLETE_EVENT_NAME',
    'maivn.events._vocabulary:PROGRESS_UPDATE_EVENT_NAME',
    'maivn.events._vocabulary:STATUS_MESSAGE_CHUNK_EVENT_NAME',
    'maivn.events._vocabulary:STATUS_MESSAGE_EVENT_NAME',
    'maivn.events._vocabulary:SYSTEM_TOOL_CHUNK_EVENT_NAME',
    'maivn.events._vocabulary:SYSTEM_TOOL_COMPLETE_EVENT_NAME',
    'maivn.events._vocabulary:SYSTEM_TOOL_ERROR_EVENT_NAME',
    'maivn.events._vocabulary:SYSTEM_TOOL_START_EVENT_NAME',
    'maivn.events._vocabulary:TOOL_EVENT_NAME',
    'maivn.events._vocabulary:UPDATE_EVENT_NAME',
)

# Protocol methods worth pinning on a class; every other dunder is implementation.
_PROTOCOL_DUNDERS = frozenset(
    {
        '__aenter__',
        '__aexit__',
        '__aiter__',
        '__anext__',
        '__await__',
        '__bool__',
        '__call__',
        '__contains__',
        '__enter__',
        '__eq__',
        '__exit__',
        '__getitem__',
        '__hash__',
        '__iter__',
        '__len__',
        '__next__',
        '__str__',
    },
)
_ADDRESS = re.compile(r' at 0x[0-9A-Fa-f]+')
# A private defining module inside a repr (`maivn._internal.serving.ServingEvent`).
_PRIVATE_PATH = re.compile(r'\bmaivn(?:\.\w+)*\._\w+(?:\.\w+)*\.(\w+)\b')
_PARAMETER_KINDS = {
    inspect.Parameter.POSITIONAL_ONLY: 'pos',
    inspect.Parameter.POSITIONAL_OR_KEYWORD: 'pos_or_kw',
    inspect.Parameter.VAR_POSITIONAL: '*',
    inspect.Parameter.KEYWORD_ONLY: 'kw',
    inspect.Parameter.VAR_KEYWORD: '**',
}


def _is_sdk(module: str) -> bool:
    return module == 'maivn' or module.startswith('maivn.')


def _clean_repr(value: object) -> str:
    return _PRIVATE_PATH.sub(r'~\1', _ADDRESS.sub('', repr(value)))


def _is_type_form(value: object) -> bool:
    """Parameterized generics and typing constructs (3.10 reports `list[int]` as a type)."""
    return typing.get_origin(value) is not None or type(value).__module__ == 'typing'


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class _Recorder:
    """Describes objects as JSON values, hoisting schema definitions into one table."""

    def __init__(self, public_names: dict[int, str]) -> None:
        self.public_names = public_names
        self.defs: dict[str, object] = {}

    def display(self, value: object) -> str:
        """Name an object without its private defining module."""
        public = self.public_names.get(id(value))
        if public is not None:
            return public
        module = cast('str', getattr(value, '__module__', '') or '')
        qualname = cast('str', getattr(value, '__qualname__', type(value).__qualname__))
        if _is_sdk(module):
            return f'~{qualname}'
        return f'{module}.{qualname}'

    def value(self, value: object) -> object:  # noqa: PLR0911 - one branch per value shape
        """Encode a constant or default deterministically."""
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, enum.Enum):
            return {'enum': f'{self.display(type(value))}.{value.name}'}
        if isinstance(value, (tuple, list)):
            key = 'tuple' if isinstance(value, tuple) else 'list'
            return {key: [self.value(item) for item in cast('tuple[object, ...]', value)]}
        if isinstance(value, (set, frozenset)):
            items = [self.value(item) for item in cast('frozenset[object]', value)]
            return {'set': sorted(items, key=_compact)}
        if isinstance(value, dict):
            pairs = [
                [self.value(key), self.value(item)]
                for key, item in cast('dict[object, object]', value).items()
            ]
            return {'dict': pairs}
        if isinstance(value, re.Pattern):
            return {'pattern': cast('re.Pattern[str]', value).pattern}
        if _is_type_form(value):
            return {'type': _clean_repr(value)}
        if isinstance(value, type):
            return {'class': self.display(value)}
        if callable(value):
            return {'callable': self.display(value)}
        return {'repr': _clean_repr(value)}

    def signature(self, value: object) -> object:
        """Parameter names, kinds and defaults; annotations are not part of the record."""
        try:
            signature = inspect.signature(cast('Any', value))
        except (TypeError, ValueError):
            return None
        parameters: list[object] = []
        for parameter in signature.parameters.values():
            entry: list[object] = [parameter.name, _PARAMETER_KINDS[parameter.kind]]
            if parameter.default is not inspect.Parameter.empty:
                entry.append(self.value(parameter.default))
            parameters.append(entry)
        return parameters

    def schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Replace inline `$defs` with digests of entries stored once in the defs table."""
        defs = cast('dict[str, object] | None', schema.pop('$defs', None))
        if defs:
            index: dict[str, str] = {}
            for name, body in defs.items():
                digest = hashlib.sha256(_compact(body).encode()).hexdigest()[:16]
                self.defs[digest] = body
                index[name] = digest
            schema['$defs'] = index
        return schema

    def describe(self, value: object, *, alias_ok: bool, path: str) -> dict[str, object]:
        """Describe one public object."""
        if inspect.ismodule(value):
            return {'kind': 'module', 'name': value.__name__}
        public = self.public_names.get(id(value))
        if alias_ok and public is not None and public != path:
            return {'kind': 'alias', 'of': public}
        if _is_type_form(value):
            return self.describe_type_form(value)
        if isinstance(value, type):
            return self.describe_class(value)
        if callable(value):
            return {
                'kind': 'async function' if inspect.iscoroutinefunction(value) else 'function',
                'signature': self.signature(value),
            }
        return {'kind': 'value', 'value': self.value(value)}

    def describe_type_form(self, value: object) -> dict[str, object]:
        """Type aliases are pinned by their JSON schema when pydantic can build one."""
        try:
            schema = pydantic.TypeAdapter(cast('Any', value)).json_schema()
        except (pydantic.PydanticUserError, pydantic.PydanticSchemaGenerationError, TypeError):
            return {'kind': 'type', 'repr': _clean_repr(value)}
        return {'kind': 'type', 'schema': self.schema(schema)}

    def describe_class(self, cls: type[object]) -> dict[str, object]:
        """Ancestry, signature, members, and the model/enum/dataclass specifics."""
        if not _is_sdk(cls.__module__):
            return {'kind': 'external class', 'name': f'{cls.__module__}.{cls.__qualname__}'}
        entry: dict[str, object] = {
            'kind': 'class',
            'mro': [self.display(base) for base in cls.__mro__[1:] if base is not object],
            'abstract': inspect.isabstract(cls),
        }
        if issubclass(cls, enum.Enum):
            entry['kind'] = 'enum'
            # `__members__`, not iteration: 3.11+ hides zero-valued flags from iteration.
            entry['members'] = {
                name: self.value(member.value) for name, member in cls.__members__.items()
            }
            return entry
        entry['signature'] = self.signature(cls)
        if issubclass(cls, pydantic.BaseModel):
            entry['kind'] = 'model'
            entry.update(self.model_details(cls))
        elif dataclasses.is_dataclass(cls):
            params = cast('Any', cls).__dataclass_params__
            entry['kind'] = 'dataclass'
            entry['dataclass'] = {
                'frozen': params.frozen,
                'eq': params.eq,
                'order': params.order,
                'fields': [self.field(field) for field in dataclasses.fields(cls)],
            }
        entry['members'] = self.members(cls)
        return entry

    def field(self, field: dataclasses.Field[object]) -> list[object]:
        """Name, init/kw_only flags and default or default factory of a dataclass field."""
        entry: list[object] = [
            field.name,
            field.init,
            cast('bool', getattr(field, 'kw_only', False)),
        ]
        if field.default is not dataclasses.MISSING:
            entry.append(self.value(field.default))
        elif field.default_factory is not dataclasses.MISSING:
            entry.append({'factory': self.value(field.default_factory)})
        return entry

    def model_details(self, cls: type[pydantic.BaseModel]) -> dict[str, object]:
        """Fields, configuration and both JSON schemas of a pydantic model."""
        details: dict[str, object] = {
            'config': self.value(dict(cls.model_config)),
            'fields': list(cls.model_fields),
        }
        for mode in ('validation', 'serialization'):
            details[f'{mode}_schema'] = self.model_schema(cls, mode)
        return details

    def model_schema(
        self,
        cls: type[pydantic.BaseModel],
        mode: typing.Literal['validation', 'serialization'],
    ) -> object:
        """Return the model's JSON schema, or the code of the error that refuses one."""
        try:
            return self.schema(cls.model_json_schema(mode=mode))
        except pydantic.PydanticUserError as error:
            return {'error': type(error).__name__, 'code': error.code}

    def members(self, cls: type[object]) -> dict[str, object]:
        """Public members the SDK itself defines on the class or its SDK ancestors."""
        found: dict[str, object] = {}
        for owner in cls.__mro__:
            if not _is_sdk(owner.__module__):
                continue
            for name, raw in vars(owner).items():
                if name in found:
                    continue
                if name.startswith('_') and name not in _PROTOCOL_DUNDERS:
                    continue
                if issubclass(cls, pydantic.BaseModel) and (
                    name in cls.model_fields or name == 'model_config'
                ):
                    continue
                found[name] = self.member(raw)
        return dict(sorted(found.items()))

    def member(self, raw: object) -> object:  # noqa: PLR0911 - one branch per descriptor type
        if raw is None:
            return {'kind': 'none'}
        if isinstance(raw, staticmethod):
            return {'kind': 'staticmethod', 'signature': self.signature(raw.__func__)}
        if isinstance(raw, classmethod):
            function = cast('Any', raw).__func__
            return {'kind': 'classmethod', 'signature': self.signature(function)}
        if isinstance(raw, property):
            return {'kind': 'property', 'settable': raw.fset is not None}
        if isinstance(raw, types.MemberDescriptorType):
            return {'kind': 'slot'}
        if type(raw).__name__ == 'cached_property':
            return {'kind': 'cached_property'}
        if inspect.isfunction(raw):
            kind = 'async method' if inspect.iscoroutinefunction(raw) else 'method'
            return {'kind': kind, 'signature': self.signature(raw)}
        if isinstance(raw, type):
            return {'kind': 'nested class', 'class': self.display(raw)}
        return {'kind': 'attribute', 'value': self.value(raw)}


def _module_names(module: object) -> list[str]:
    """`__all__` in order, or the module's own top-level public definitions, sorted."""
    exported = cast('list[str] | None', getattr(module, '__all__', None))
    if exported is not None:
        return list(exported)
    source = Path(cast('str', getattr(module, '__file__', ''))).read_text(encoding='utf-8')
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return sorted(name for name in names if not name.startswith('_'))


def _from_import(module: types.ModuleType, name: str) -> object:
    """Resolve `from module import name`, which falls back to importing a submodule."""
    try:
        return getattr(module, name)
    except AttributeError:
        return importlib.import_module(f'{module.__name__}.{name}')


def _public_names() -> dict[int, str]:
    """Return the first public path of every class and callable, in module and export order."""
    names: dict[int, str] = {}
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        for name in _module_names(module):
            value = getattr(module, name)
            if isinstance(value, type) or inspect.isfunction(value) or inspect.isbuiltin(value):
                names.setdefault(id(value), f'{module_name}:{name}')
    return names


def _render() -> tuple[str, str, str]:
    recorder = _Recorder(_public_names())
    surface: list[str] = []
    for module_name in PUBLIC_MODULES:
        module = importlib.import_module(module_name)
        exported = getattr(module, '__all__', None)
        surface.append(_compact({'module': module_name, 'has_all': exported is not None}))
        for name in _module_names(module):
            path = f'{module_name}:{name}'
            value = getattr(module, name)
            if name == '__version__':
                description: dict[str, object] = {'kind': 'value', 'value': '<release version>'}
            else:
                description = recorder.describe(value, alias_ok=True, path=path)
            surface.append(_compact({'path': path, **description}))
    consumer: list[str] = []
    for reference in CONSUMER_PRIVATE_NAMES:
        module_name, _, name = reference.partition(':')
        module = importlib.import_module(module_name)
        value = _from_import(module, name) if name else module
        description = recorder.describe(value, alias_ok=True, path=reference)
        consumer.append(_compact({'path': reference, **description}))
    defs = [
        _compact({'digest': digest, 'schema': body})
        for digest, body in sorted(recorder.defs.items())
    ]
    return (
        '\n'.join(surface) + '\n',
        '\n'.join(consumer) + '\n',
        '\n'.join(defs) + '\n',
    )


def _changed_paths(live: str, committed: str) -> list[str]:
    def keyed(text: str) -> dict[str, str]:
        lines: dict[str, str] = {}
        for line in text.splitlines():
            record = cast('dict[str, object]', json.loads(line))
            key = record.get('path') or record.get('module') or record.get('digest')
            lines[str(key)] = line
        return lines

    old, new = keyed(committed), keyed(live)
    changed = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
    if not changed and live != committed:
        return ['<line order>']
    return changed


def test_public_modules_are_all_recorded() -> None:
    """A new public module (no underscore in its dotted path) must join the record."""
    found = {'maivn', 'maivn.__version__'}
    for info in pkgutil.walk_packages(maivn.__path__, 'maivn.'):
        if not any(part.startswith('_') for part in info.name.split('.')):
            found.add(info.name)

    assert found == set(PUBLIC_MODULES)


def test_public_surface_matches_the_snapshot() -> None:
    """Every public name, signature, member and schema is unchanged."""
    live = dict(zip((SURFACE_PATH, CONSUMER_PATH, DEFS_PATH), _render(), strict=True))
    if os.environ.get(WRITE_ENV) == '1':
        for path, text in live.items():
            path.write_text(text, encoding='utf-8', newline='\n')

    changed = {
        path.name: _changed_paths(text, path.read_text(encoding='utf-8'))
        for path, text in live.items()
    }
    assert not any(changed.values()), (
        f'public surface changed: {changed}; if intended, rerun with {WRITE_ENV}=1 '
        'and review the diff (a public API change needs owner approval)'
    )
