"""Characterize the ``maivn`` console command as a person reads it off the terminal.

Every test drives ``maivn.cli.main`` through ``sys.argv`` and pins the exit code
and the exact stdout/stderr text. Nothing here launches Studio, pip, a real
cloudflared or a network request: Studio is a throwaway package on ``sys.path``,
pip and Studio launches go through a recording ``subprocess.run``, and the
tunnel child is a short Python script standing in for cloudflared.
"""

from __future__ import annotations

import builtins
import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from maivn import ServingEvent, cli
from maivn.__version__ import __version__
from maivn.dev_tunnel import CLOUDFLARED_VERSION

if TYPE_CHECKING:
    import ssl
    from collections.abc import Callable, Iterator

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_SIGINT = 130

MAIN_HELP = (
    'MAIVN SDK Command Line Interface\n'
    '\n'
    'Usage: maivn <command> [options]\n'
    '\n'
    'Commands:\n'
    '  serve     Serve a local agent and announce it to the platform\n'
    '  dev-tunnel Open a temporary HTTPS tunnel for selected local receivers\n'
    '  watch-files Watch local file metadata and durably deliver signed change events\n'
    '  connections Create a saved connection with a temporary human login\n'
    '  studio    Launch MAIVN Studio - UI/UX developer tool\n'
    '  version   Show MAIVN SDK version\n'
    '\n'
    'Run "maivn serve --help" for serving options.\n'
    'Run "maivn dev-tunnel --help" for local webhook receiver options.\n'
    'Run "maivn watch-files --help" for explicit local file observation.\n'
    'Run "maivn studio --help" for studio-specific options.\n'
)

SERVE_HELP = (
    'Usage: maivn serve <module|path.py>[:attribute] [options]\n'
    '\n'
    'Announce a locally running agent to the platform and keep it announced.\n'
    'The agent runs here, in this process. The platform learns its identity\n'
    'and a fingerprint of its definition, never the definition itself.\n'
    '\n'
    'Options:\n'
    '  --project-id ID     Project to announce into (or MAIVN_PROJECT_ID)\n'
    '  --agent-id ID       Agent id to announce as (default: agent ID or name)\n'
    '  --version N         Version this process serves (default: agent version)\n'
    '  --transport NAME    worker or webhook (default: worker)\n'
    '  --endpoint-url URL  HTTPS endpoint, required by --transport webhook\n'
    '\n'
    'Examples:\n'
    '  maivn serve app.py\n'
    '  maivn serve myapp.agents:triage --version 3\n'
)

STUDIO_MISSING = (
    '[ERROR] Cannot launch MAIVN Studio: maivn_studio is not installed\n'
    '[INFO] Make sure the Studio companion is installed:\n'
    '       uv pip install maivn-studio\n'
)

_SINGLE_AGENT = """
from maivn import Agent

triage = Agent(name='triage', api_key='test-key')
"""


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
    """Run ``maivn <args>`` and return the exit code (None when main returns)."""
    monkeypatch.setattr(sys, 'argv', ['maivn', *args])
    try:
        cli.main()
    except SystemExit as stopped:
        return stopped.code
    return None


@pytest.fixture
def module_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Give agent modules a private import root and forget them afterwards."""
    monkeypatch.setattr(sys, 'path', [str(tmp_path), *sys.path])
    monkeypatch.delenv('MAIVN_PROJECT_ID', raising=False)
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        if name.startswith('cli_char_'):
            _ = sys.modules.pop(name, None)


@pytest.fixture
def no_studio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    """Record subprocess launches; Studio stays uninstalled unless a test installs it."""
    launches: list[list[str]] = []

    def record(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001 - matches subprocess.run's call shape
    ) -> subprocess.CompletedProcess[bytes]:
        launches.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0)

    monkeypatch.setattr(subprocess, 'run', record)
    monkeypatch.setattr(sys, 'path', [str(tmp_path / 'studio-root'), *sys.path])
    return launches


def _install_fake_studio(tmp_path: Path) -> None:
    package = tmp_path / 'studio-root' / 'maivn_studio'
    package.mkdir(parents=True)
    _ = (package / '__init__.py').write_text('', encoding='utf-8')


class _Terminal:
    """A stdin that reports a terminal, the way an interactive shell does."""

    def isatty(self) -> bool:
        """Report an attached terminal."""
        return True


def _interactive(monkeypatch: pytest.MonkeyPatch, answer: Callable[[str], str]) -> list[str]:
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return answer(prompt)

    monkeypatch.setattr(sys, 'stdin', _Terminal())
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(builtins, 'input', ask)
    return prompts


# --- top-level dispatch -------------------------------------------------------------------


@pytest.mark.parametrize('argv', [(), ('-h',), ('--help',)])
def test_help_is_printed_to_stdout_for_no_command_or_a_help_flag(
    argv: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bare `maivn` and its help flags print the command table and exit cleanly."""
    assert _run(monkeypatch, *argv) is None
    assert capsys.readouterr() == (MAIN_HELP, '')


def test_unknown_command_names_itself_and_prints_help_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A typo exits 1 with the command named, and the help goes to stderr, not stdout."""
    assert _run(monkeypatch, 'serv', 'app.py') == EXIT_ERROR
    assert capsys.readouterr() == ('', f'[ERROR] Unknown command: serv\n\n{MAIN_HELP}')


def test_version_prints_the_installed_sdk_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`maivn version` is one line naming the SDK version."""
    assert _run(monkeypatch, 'version') is None
    assert capsys.readouterr() == (f'maivn {__version__}\n', '')


# --- serve --------------------------------------------------------------------------------


@pytest.mark.parametrize('argv', [('serve',), ('serve', '-h'), ('serve', '--help')])
def test_serve_help_is_printed_without_a_target(
    argv: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`maivn serve` with no target, or a help flag, prints the serve usage to stdout."""
    assert _run(monkeypatch, *argv) is None
    assert capsys.readouterr() == (SERVE_HELP, '')


@pytest.mark.parametrize(
    ('options', 'first_line'),
    [
        (['--nope', 'x'], '[ERROR] Unknown serve option: --nope'),
        (['--project-id=p', '--agent-id'], '[ERROR] Missing value for --agent-id'),
        (['--version='], '[ERROR] --version must be a positive whole number'),
        (['--version', '-1'], '[ERROR] --version must be a positive whole number'),
        (['--version=0'], '[ERROR] --version must be a positive whole number'),
        (['--transport=WORKER'], '[ERROR] --transport must be one of: worker, webhook'),
        (['app.py'], '[ERROR] Unknown serve option: app.py'),
    ],
)
def test_serve_refuses_a_bad_option_with_its_reason_then_usage(
    options: list[str],
    first_line: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first unusable option is named on stderr, followed by the serve usage."""
    assert _run(monkeypatch, 'serve', 'app.py', *options) == EXIT_ERROR
    assert capsys.readouterr() == ('', f'{first_line}\n\n{SERVE_HELP}')


def test_serve_reports_a_missing_module_file_by_the_name_given(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A `.py` target that is not a file is refused before anything is imported."""
    missing = tmp_path / 'absent_agent.py'

    assert _run(monkeypatch, 'serve', str(missing)) == EXIT_ERROR
    assert capsys.readouterr() == ('', f'[ERROR] no such agent module file: {missing}\n')


def test_serve_treats_an_existing_directory_as_a_path_not_a_module(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """An existing path is never handed to import, and a directory is not a module file."""
    folder = tmp_path / 'agents'
    folder.mkdir()

    assert _run(monkeypatch, 'serve', str(folder)) == EXIT_ERROR
    assert capsys.readouterr() == ('', f'[ERROR] no such agent module file: {folder}\n')


def test_serve_reports_an_unimportable_dotted_module(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dotted name that does not import is reported with the import error text."""
    assert _run(monkeypatch, 'serve', 'cli_char_missing.agents:triage') == EXIT_ERROR
    assert capsys.readouterr() == ('', "[ERROR] No module named 'cli_char_missing'\n")


def test_serve_reports_a_module_without_the_named_agent(
    module_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`module:name` naming nothing is an error that names the module and the attribute."""
    _ = (module_dir / 'cli_char_named.py').write_text(_SINGLE_AGENT, encoding='utf-8')

    assert _run(monkeypatch, 'serve', 'cli_char_named:billing') == EXIT_ERROR
    assert capsys.readouterr() == ('', '[ERROR] cli_char_named has no agent named billing\n')


def test_serve_lists_every_candidate_when_a_module_is_ambiguous(
    module_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Several agents are listed in sorted order with the exact spelling to pick one."""
    source = _SINGLE_AGENT + "zeta = Agent(name='zeta', api_key='test-key')\n"
    _ = (module_dir / 'cli_char_many.py').write_text(source, encoding='utf-8')

    assert _run(monkeypatch, 'serve', 'cli_char_many') == EXIT_ERROR
    assert capsys.readouterr() == (
        '',
        '[ERROR] cli_char_many defines several agents (triage, zeta); '
        'pick one with cli_char_many:<name>\n',
    )


def test_serve_without_a_project_explains_how_to_supply_one(
    module_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dotted module serves its only agent; no project id fails before any request."""
    _ = (module_dir / 'cli_char_served.py').write_text(_SINGLE_AGENT, encoding='utf-8')

    assert _run(monkeypatch, 'serve', 'cli_char_served') == EXIT_ERROR
    assert capsys.readouterr() == (
        '',
        '[ERROR] serving requires a project id: pass project_id= to serve(), '
        'or set the MAIVN_PROJECT_ID environment variable\n',
    )


def test_serve_refuses_a_webhook_without_an_endpoint(
    module_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A transport that nothing could reach is refused with the reason, exit 1."""
    path = module_dir / 'cli_char_webhook.py'
    _ = path.write_text(_SINGLE_AGENT, encoding='utf-8')

    code = _run(
        monkeypatch, 'serve', f'{path}:triage', '--project-id', 'prj', '--transport', 'webhook'
    )

    assert code == EXIT_ERROR
    assert capsys.readouterr() == (
        '',
        '[ERROR] webhook transport requires endpoint_url: the platform reaches a webhook '
        'process by URL, so a manifest without one announces a version nothing can call\n',
    )


@pytest.mark.parametrize(
    ('event', 'stream', 'line'),
    [
        (
            ServingEvent(
                kind='re_announced',
                agent_id='triage',
                version=2,
                instance_id='inst-9',
                process_id='prc-9',
            ),
            'out',
            '[INFO] Re-announced triage v2 as instance inst-9 (process prc-9); '
            'heartbeat every 25s. Press Ctrl-C to stop.',
        ),
        (
            ServingEvent(kind='announced', agent_id='triage', version=1, instance_id='inst-1'),
            'out',
            '[INFO] Announced triage v1 as instance inst-1 (process None); '
            'heartbeat every 25s. Press Ctrl-C to stop.',
        ),
        (
            ServingEvent(
                kind='retrying',
                agent_id='triage',
                version=2,
                instance_id='inst-9',
                detail='connection refused',
            ),
            'err',
            '[INFO] Platform unreachable for triage v2: connection refused',
        ),
        (
            ServingEvent(
                kind='unreachable',
                agent_id='triage',
                version=2,
                instance_id='inst-9',
                detail='timed out',
            ),
            'err',
            '[ERROR] Missed a heartbeat for triage v2: timed out. '
            'Still serving; retrying on the next beat.',
        ),
        (
            ServingEvent(
                kind='stopped',
                agent_id='triage',
                version=2,
                instance_id='inst-9',
                detail='revoked',
            ),
            'out',
            '[INFO] Stopped serving triage v2 (revoked).',
        ),
        (
            ServingEvent(kind='stopped', agent_id='triage', version=2, instance_id='inst-9'),
            'out',
            '[INFO] Stopped serving triage v2.',
        ),
    ],
)
def test_each_serving_event_prints_one_line_on_its_stream(
    event: ServingEvent,
    stream: str,
    line: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Liveness goes to stdout, trouble goes to stderr, one exact line per event."""
    cli._report_serving_event(event)  # noqa: SLF001 # pyright: ignore[reportPrivateUsage] - the serve listener

    captured = capsys.readouterr()
    expected = {'out': '', 'err': ''}
    expected[stream] = line + '\n'
    assert (captured.out, captured.err) == (expected['out'], expected['err'])


# --- studio -------------------------------------------------------------------------------


def test_studio_missing_in_a_pipeline_prints_the_install_hint(
    no_studio: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a terminal there is no prompt: the hint is printed and nothing is run."""
    assert _run(monkeypatch, 'studio') == EXIT_ERROR
    assert capsys.readouterr() == ('', STUDIO_MISSING)
    assert no_studio == []


@pytest.mark.parametrize('answer', ['', 'n', 'no', 'maybe'])
def test_studio_install_offer_declined_prints_how_to_install_later(
    answer: str,
    no_studio: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Anything but yes declines: exit 1, the later-install hint, and nothing installed."""
    prompts = _interactive(monkeypatch, lambda _prompt: answer)

    assert _run(monkeypatch, 'studio') == EXIT_ERROR
    assert prompts == ['[INFO] MAIVN Studio is not installed. Install it now? [y/N]: ']
    assert capsys.readouterr().err == '[INFO] To install it later: uv pip install maivn-studio\n'
    assert no_studio == []


@pytest.mark.parametrize('interrupt', [EOFError, KeyboardInterrupt])
def test_studio_install_offer_interrupted_counts_as_no(
    interrupt: type[BaseException],
    no_studio: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ctrl-D or Ctrl-C at the prompt ends the line and declines rather than crashing."""

    def interrupted(_prompt: str) -> str:
        raise interrupt

    _ = _interactive(monkeypatch, interrupted)

    assert _run(monkeypatch, 'studio') == EXIT_ERROR
    assert capsys.readouterr().err == (
        '\n[INFO] To install it later: uv pip install maivn-studio\n'
    )
    assert no_studio == []


@pytest.mark.parametrize('answer', ['y', ' YES '])
def test_studio_install_accepted_installs_then_launches_with_arguments(
    answer: str,
    no_studio: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Yes installs with this interpreter's pip, then launches Studio's module with args."""
    _ = _interactive(monkeypatch, lambda _prompt: answer)

    assert _run(monkeypatch, 'studio', '--port', '9000') == 0
    assert no_studio == [
        [sys.executable, '-m', 'pip', 'install', 'maivn-studio'],
        [sys.executable, '-m', 'maivn_studio.main', '--port', '9000'],
    ]
    assert capsys.readouterr().err == '[INFO] Installing maivn-studio...\n'


def test_studio_failed_install_says_how_to_install_manually(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A failed pip install exits 1 with the manual command and never launches."""
    launches: list[list[str]] = []

    def failing(
        command: list[str],
        *,
        check: bool = False,  # noqa: ARG001 - matches subprocess.run's call shape
    ) -> subprocess.CompletedProcess[bytes]:
        launches.append(command)
        return subprocess.CompletedProcess(args=command, returncode=1)

    monkeypatch.setattr(subprocess, 'run', failing)
    monkeypatch.setattr(sys, 'path', [str(tmp_path / 'studio-root'), *sys.path])
    _ = _interactive(monkeypatch, lambda _prompt: 'y')

    assert _run(monkeypatch, 'studio') == EXIT_ERROR
    assert capsys.readouterr().err == (
        '[INFO] Installing maivn-studio...\n'
        '[ERROR] Automatic install failed. Install it manually:\n'
        '       uv pip install maivn-studio\n'
    )
    assert launches == [[sys.executable, '-m', 'pip', 'install', 'maivn-studio']]


def test_installed_studio_launches_directly_and_exits_with_its_code(
    no_studio: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """An importable Studio is launched without any prompt, passing every argument through."""
    _install_fake_studio(tmp_path)

    assert _run(monkeypatch, 'studio', '--help') == 0
    assert no_studio == [[sys.executable, '-m', 'maivn_studio.main', '--help']]
    assert capsys.readouterr() == ('', '')


def test_ctrl_c_during_studio_exits_130(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Interrupting a running Studio exits with the conventional SIGINT status, silently."""
    _install_fake_studio(tmp_path)
    monkeypatch.setattr(sys, 'path', [str(tmp_path / 'studio-root'), *sys.path])

    def interrupted(
        command: list[str],  # noqa: ARG001 - matches subprocess.run's call shape
        *,
        check: bool = False,  # noqa: ARG001 - matches subprocess.run's call shape
    ) -> subprocess.CompletedProcess[bytes]:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, 'run', interrupted)

    assert _run(monkeypatch, 'studio') == EXIT_SIGINT
    assert capsys.readouterr() == ('', '')


# --- dev-tunnel ---------------------------------------------------------------------------


@pytest.fixture
def tool_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the managed tools root at a private home on a known supported platform."""
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('PATH', str(tmp_path / 'empty-path'))
    monkeypatch.setattr(platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(platform, 'machine', lambda: 'x86_64')
    return home


def _serve_download(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> list[str]:
    """Answer the pinned GitHub release URL from memory with a matching checksum."""
    from maivn._internal import cloudflared_install  # noqa: PLC0415 - the pinned digest table

    monkeypatch.setitem(
        cloudflared_install.ASSETS,
        ('Linux', 'amd64'),
        ('cloudflared-linux-amd64', hashlib.sha256(payload).hexdigest()),
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=payload)

    original = httpx.Client

    def client(*, timeout: float, follow_redirects: bool, verify: ssl.SSLContext) -> httpx.Client:
        return original(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=verify,
        )

    monkeypatch.setattr(httpx, 'Client', client)
    return requested


def test_dev_tunnel_install_reports_the_ready_path_and_opens_nothing(
    tool_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--install` downloads the pinned asset, prints where it is, and opens no tunnel."""
    requested = _serve_download(monkeypatch, b'verified client bytes')
    installed = (
        tool_home
        / '.maivn'
        / 'tools'
        / 'cloudflared'
        / CLOUDFLARED_VERSION
        / 'cloudflared-linux-amd64'
        / 'cloudflared'
    )

    assert _run(monkeypatch, 'dev-tunnel', '--install') is None
    assert capsys.readouterr() == (
        '[INFO] Installing the verified official cloudflared client...\n'
        f'[INFO] Ready: {installed}. No tunnel is open.\n'
        'Start with: maivn dev-tunnel --connection-id YOUR_CONNECTION_ID\n',
        '',
    )
    assert installed.read_bytes() == b'verified client bytes'
    assert requested == [
        'https://github.com/cloudflare/cloudflared/releases/download/'
        f'{CLOUDFLARED_VERSION}/cloudflared-linux-amd64'
    ]


def test_dev_tunnel_install_on_an_unsupported_platform_exits_1(
    tool_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An install failure is one error line on stderr after the progress line."""
    monkeypatch.setattr(platform, 'machine', lambda: 'riscv64')

    assert _run(monkeypatch, 'dev-tunnel', '--install') == EXIT_ERROR
    assert capsys.readouterr() == (
        '[INFO] Installing the verified official cloudflared client...\n',
        '[ERROR] Automatic cloudflared installation is not supported on this platform. '
        'Install an official compatible client on PATH.\n',
    )
    assert not (tool_home / '.maivn').exists()


def test_dev_tunnel_refuses_install_combined_with_exposure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Install and expose are separate steps: asking for both is a usage error."""
    assert _run(monkeypatch, 'dev-tunnel', '--install', '--connection-id', 'conn-a') == EXIT_USAGE
    assert capsys.readouterr() == (
        '',
        'usage: maivn dev-tunnel [-h] [--connection-id CONNECTION_ID] [--install]\n'
        'maivn dev-tunnel: error: Install separately, then start with --connection-id.\n',
    )


def test_dev_tunnel_without_cloudflared_says_how_to_install(
    tool_home: Path,  # noqa: ARG001 - isolates PATH and the managed tools root
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing client is reported before any listener is opened, with both remedies."""
    assert _run(monkeypatch, 'dev-tunnel', '--connection-id', 'conn-a') == EXIT_ERROR
    assert capsys.readouterr() == (
        '',
        '[ERROR] cloudflared is not installed. Install the open-source client from '
        'https://developers.cloudflare.com/cloudflare-one/networks/connectors/'
        'cloudflare-tunnel/downloads/ and make cloudflared available on PATH. '
        'No Cloudflare account is needed. Or run: maivn dev-tunnel --install\n',
    )


def _fake_cloudflared_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> list[list[str]]:
    """Put a `cloudflared` on PATH whose launch runs a Python stand-in script."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    name = 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'
    executable = bin_dir / name
    _ = executable.write_text('stand-in', encoding='utf-8')
    executable.chmod(0o700)
    stand_in = tmp_path / 'stand_in.py'
    _ = stand_in.write_text(script, encoding='utf-8')
    monkeypatch.setenv('PATH', str(bin_dir))
    launched: list[list[str]] = []
    original = subprocess.Popen

    def launch(args: list[str], **options: object) -> subprocess.Popen[str]:
        launched.append(list(args))
        assert Path(args[0]).name.lower() == name
        return original([sys.executable, str(stand_in), *args[1:]], **options)  # pyright: ignore[reportCallIssue, reportArgumentType]

    monkeypatch.setattr(subprocess, 'Popen', launch)
    return launched


def test_dev_tunnel_prints_receiver_urls_and_reports_a_closed_tunnel(
    tool_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each selected receiver is listed under the public origin; child exit is an error."""
    del tool_home
    launched = _fake_cloudflared_on_path(
        tmp_path,
        monkeypatch,
        "import time\nprint('https://char-proof.trycloudflare.com', flush=True)\ntime.sleep(0.5)\n",
    )

    code = _run(monkeypatch, 'dev-tunnel', '--connection-id', 'conn-b', '--connection-id', 'conn-a')

    assert code == EXIT_ERROR
    assert capsys.readouterr() == (
        '[INFO] Temporary HTTPS receiver URLs (Cloudflare Quick Tunnel):\n'
        '  https://char-proof.trycloudflare.com/v1/connections/conn-a/events\n'
        '  https://char-proof.trycloudflare.com/v1/connections/conn-b/events\n'
        '[INFO] Only these signed POST routes reach the local relay.\n'
        '[INFO] Keep maivn serve running locally. Press Ctrl-C to close the tunnel.\n',
        '[ERROR] cloudflared exited (code 0); the temporary tunnel is closed.\n',
    )
    assert len(launched) == 1
    assert launched[0][1:4] == ['tunnel', '--config', launched[0][3]]
    assert launched[0][4:6] == ['--no-autoupdate', '--url']
    assert launched[0][6].startswith('http://127.0.0.1:')
    assert launched[0][7:] == ['--loglevel', 'info']


def test_dev_tunnel_refuses_an_unsafe_connection_id_before_listening(
    tool_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator input outside the receiver grammar is an error line, never a listener."""
    del tool_home
    launched = _fake_cloudflared_on_path(tmp_path, monkeypatch, 'raise SystemExit(0)\n')

    assert _run(monkeypatch, 'dev-tunnel', '--connection-id', '../admin') == EXIT_ERROR
    assert capsys.readouterr() == (
        '',
        '[ERROR] Provide connection IDs of 1-200 characters, starting with an ASCII letter '
        'or digit and containing only ASCII letters, digits, ., _, : and -.\n',
    )
    assert launched == []
