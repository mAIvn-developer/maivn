"""Contracts for ``maivn serve``: load the developer's agent, then serve it.

The command is the terminal face of "we do not host agents" - the process the
developer starts here *is* the agent. So the things pinned below are the ones a
person reads off the screen: that it announced, that it is heartbeating, that it
stopped cleanly, and that none of that ever includes their API key.
"""

from __future__ import annotations

import sys
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Agent, Client, ClientConfig, ServingEvent, cli

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_SINGLE_AGENT = """
from maivn import Agent

triage = Agent(name='triage', api_key='test-key')
"""

_TWO_AGENTS = """
from maivn import Agent

triage = Agent(name='triage', api_key='test-key')
billing = Agent(name='billing', api_key='test-key')
"""

_NO_AGENT = """
VALUE = 1
"""

# Distinctive enough that a leak into stdout or stderr would be unmistakable.
_CREDENTIAL = 'unmistakable-synthetic-credential'
_PROJECT_ID = '11111111-1111-1111-1111-111111111111'
_PARSED_VERSION = 3


@pytest.fixture
def agent_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str, str], Path]]:
    """Write throwaway agent modules and undo what importing them leaves behind."""
    # _load_agent_module prepends the file's directory, the way `python app.py`
    # does; swapping in a copy of sys.path keeps that out of the rest of the run.
    monkeypatch.setattr(sys, 'path', [*sys.path])
    created: list[str] = []

    def write(stem: str, source: str) -> Path:
        path = tmp_path / f'{stem}.py'
        _ = path.write_text(source, encoding='utf-8')
        created.append(stem)
        return path

    yield write

    for stem in created:
        _ = sys.modules.pop(stem, None)


def _serving_agent(handler: Callable[[httpx.Request], httpx.Response]) -> Agent:
    return Agent(
        name='triage',
        client=Client(
            config=ClientConfig(api_key=_CREDENTIAL, base_url=AnyUrl('https://control.example')),
            transport=httpx.MockTransport(handler),
        ),
    )


def _serve_request() -> cli.ServeRequest:
    return cli.ServeRequest(
        target='app.py',
        project_id=_PROJECT_ID,
        agent_id=None,
        version=1,
        transport='worker',
        endpoint_url=None,
    )


def test_serve_is_offered_alongside_the_other_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """A developer discovers serving from the CLI's own help."""
    cli._print_help()  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    output = capsys.readouterr().out
    assert 'serve' in output
    assert 'maivn serve --help' in output


def test_serve_help_names_the_project_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`maivn serve --help` routes through main and says where the project comes from."""
    monkeypatch.setattr(sys, 'argv', ['maivn', 'serve', '--help'])

    cli.main()

    output = capsys.readouterr().out
    assert 'Usage: maivn serve' in output
    assert 'MAIVN_PROJECT_ID' in output
    assert '--transport' in output


def test_serve_loads_the_only_agent_in_a_module_file(
    agent_module: Callable[[str, str], Path],
) -> None:
    """A file with exactly one agent needs no attribute to disambiguate it."""
    path = agent_module('serve_single_agent', _SINGLE_AGENT)

    loaded = cli._load_agent(str(path))  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert loaded.name == 'triage'


def test_serve_loads_a_named_agent_from_a_module_file(
    agent_module: Callable[[str, str], Path],
) -> None:
    """`module:name` picks one agent out of several."""
    path = agent_module('serve_two_agents', _TWO_AGENTS)

    loaded = cli._load_agent(f'{path}:billing')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert loaded.name == 'billing'


def test_serve_refuses_to_guess_between_several_agents(
    agent_module: Callable[[str, str], Path],
) -> None:
    """Serving the wrong agent silently is worse than asking which one."""
    path = agent_module('serve_ambiguous', _TWO_AGENTS)

    with pytest.raises(ValueError, match='several agents'):
        _ = cli._load_agent(str(path))  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test


def test_serve_says_so_when_a_module_defines_no_agent(
    agent_module: Callable[[str, str], Path],
) -> None:
    """The error names the module rather than failing somewhere deeper."""
    path = agent_module('serve_empty', _NO_AGENT)

    with pytest.raises(ValueError, match='no maivn Agent'):
        _ = cli._load_agent(str(path))  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test


def test_serve_says_so_when_the_named_agent_is_missing(
    agent_module: Callable[[str, str], Path],
) -> None:
    """A typo in `module:name` is reported, not treated as an empty module."""
    path = agent_module('serve_named_missing', _SINGLE_AGENT)

    with pytest.raises(ValueError, match='no agent named'):
        _ = cli._load_agent(f'{path}:missing')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test


def test_serve_target_is_not_split_on_a_windows_drive_letter() -> None:
    r"""`C:\work\app.py` is one path, not module `C` with an attribute."""
    assert cli._split_agent_target(r'C:\work\app.py') == (r'C:\work\app.py', '')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test
    assert cli._split_agent_target(r'C:\work\app.py:triage') == (r'C:\work\app.py', 'triage')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test
    assert cli._split_agent_target('myapp.agents:triage') == ('myapp.agents', 'triage')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test


def test_serve_missing_module_file_is_reported_by_name() -> None:
    """A path that does not exist fails on the path, not on an import guess."""
    with pytest.raises(FileNotFoundError, match='no such agent module file'):
        _ = cli._load_agent('nope_missing_agent_module.py')  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test


def test_serve_accepts_both_option_spellings() -> None:
    """`--flag value` and `--flag=value` mean the same thing."""
    request = cli._parse_serve_request(  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test
        [
            'app.py',
            '--project-id',
            'prj-1',
            '--agent-id=triage',
            '--version',
            str(_PARSED_VERSION),
            '--transport=webhook',
            '--endpoint-url=https://hook.example/invoke',
        ],
    )

    assert request is not None
    assert request.target == 'app.py'
    assert request.project_id == 'prj-1'
    assert request.agent_id == 'triage'
    assert request.version == _PARSED_VERSION
    assert request.transport == 'webhook'
    assert request.endpoint_url == 'https://hook.example/invoke'


def test_serve_defaults_to_the_worker_transport_and_owner_version() -> None:
    """An omitted version preserves the version configured on the agent."""
    request = cli._parse_serve_request(['app.py'])  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert request is not None
    assert request.transport == 'worker'
    assert request.version is None
    assert request.project_id is None


@pytest.mark.parametrize(
    ('args', 'expected'),
    [
        (['app.py', '--nope', 'x'], 'Unknown serve option'),
        (['app.py', '--project-id'], 'Missing value'),
        (['app.py', '--version', 'three'], '--version must be'),
        (['app.py', '--version', '0'], '--version must be'),
        (['app.py', '--transport', 'carrier-pigeon'], '--transport must be'),
    ],
)
def test_serve_reports_the_first_unusable_argument(
    args: list[str],
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bad arguments are refused with the reason, never absorbed into a default."""
    request = cli._parse_serve_request(args)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert request is None
    assert expected in capsys.readouterr().err


def test_serve_reports_liveness_a_person_can_read(capsys: pytest.CaptureFixture[str]) -> None:
    """Announced, heartbeating, stopped - visible from the output alone."""
    events = (
        ServingEvent(
            kind='announced',
            agent_id='triage',
            version=1,
            instance_id='inst-1',
            process_id='prc-1',
        ),
        ServingEvent(
            kind='heartbeat',
            agent_id='triage',
            version=1,
            instance_id='inst-1',
            process_id='prc-1',
        ),
        ServingEvent(kind='stopped', agent_id='triage', version=1, instance_id='inst-1'),
    )

    for event in events:
        cli._report_serving_event(event)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    output = capsys.readouterr().out
    assert 'Announced triage v1' in output
    assert 'Heartbeat accepted' in output
    assert 'Stopped serving triage v1' in output
    assert 'Ctrl-C' in output


def test_serve_explains_a_refusal_without_printing_the_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 401 exits non-zero, says what to check, and keeps the credential off screen."""

    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            HTTPStatus.UNAUTHORIZED,
            json={'detail': 'Authentication required.', 'code': 'unauthorized'},
        )

    with pytest.raises(SystemExit) as exit_info:
        cli._serve_agent(_serving_agent(refuse), _serve_request())  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert exit_info.value.code == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert '401' in captured.err
    assert 'MAIVN_API_KEY' in captured.err
    assert _CREDENTIAL not in captured.err
    assert _CREDENTIAL not in captured.out


def test_serve_reports_a_clean_stop_on_ctrl_c(capsys: pytest.CaptureFixture[str]) -> None:
    """Ctrl-C says the agent stopped rather than dumping a traceback."""

    def interrupt(_request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exit_info:
        cli._serve_agent(_serving_agent(interrupt), _serve_request())  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - unit under test

    assert exit_info.value.code == cli.EXIT_SIGINT
    assert 'Stopped serving triage' in capsys.readouterr().err
