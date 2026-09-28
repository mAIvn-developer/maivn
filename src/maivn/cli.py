"""MAIVN CLI - Unified command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module
from importlib.util import find_spec, module_from_spec, spec_from_file_location
from pathlib import Path
from typing import TYPE_CHECKING, Final, TextIO, cast

from maivn import Agent, MaivnHTTPError, MaivnSDKError
from maivn.__version__ import __version__
from maivn._internal.cloudflared_install import install_cloudflared
from maivn._internal.config import LOCAL_BASE_URL, PROJECT_ID_ENV_VAR
from maivn._internal.connection_setup_cli import run_connections
from maivn._internal.dev_tunnel import CloudflaredTunnel, ReceiverProxy, require_cloudflared
from maivn._internal.local_file_cli import run_watch_files
from maivn._internal.serving import (
    HEARTBEAT_INTERVAL_SECONDS,
    SERVING_TRANSPORTS,
    ServingEvent,
)

if TYPE_CHECKING:
    from types import ModuleType

    from maivn_contracts.agents import ServingTransport

COMMAND_SERVE: Final = 'serve'
COMMAND_DEV_TUNNEL: Final = 'dev-tunnel'
COMMAND_WATCH_FILES: Final = 'watch-files'
COMMAND_CONNECTIONS: Final = 'connections'
COMMAND_STUDIO: Final = 'studio'
COMMAND_VERSION: Final = 'version'
HELP_FLAGS: Final[frozenset[str]] = frozenset({'-h', '--help'})
COMMAND_HELP: Final[tuple[tuple[str, str], ...]] = (
    (COMMAND_SERVE, 'Serve a local agent and announce it to the platform'),
    (COMMAND_DEV_TUNNEL, 'Open a temporary HTTPS tunnel for selected local receivers'),
    (COMMAND_WATCH_FILES, 'Watch local file metadata and durably deliver signed change events'),
    (COMMAND_CONNECTIONS, 'Create a saved connection with a temporary human login'),
    (COMMAND_STUDIO, 'Launch MAIVN Studio - UI/UX developer tool'),
    (COMMAND_VERSION, 'Show MAIVN SDK version'),
)

STUDIO_PACKAGE: Final = 'maivn-studio'  # the PyPI package name, not a command
STUDIO_MODULE: Final = 'maivn_studio'
STUDIO_MODULE_MAIN: Final = 'maivn_studio.main'

SERVE_FLAGS: Final[frozenset[str]] = frozenset(
    {'--project-id', '--agent-id', '--version', '--transport', '--endpoint-url'},
)

ERROR_PREFIX: Final = '[ERROR]'
INFO_PREFIX: Final = '[INFO]'
EXIT_ERROR: Final = 1
EXIT_SIGINT: Final = 130


@dataclass(frozen=True, slots=True)
class ServeRequest:
    """One validated ``maivn serve`` invocation."""

    target: str
    project_id: str | None
    agent_id: str | None
    version: int | None
    transport: ServingTransport
    endpoint_url: str | None


def main() -> None:
    """Run the maivn CLI.

    Usage:
        maivn serve <module>    - Serve a local agent
        maivn studio [args...]  - Launch MAIVN Studio
        maivn version           - Show SDK version
        maivn --help            - Show help
    """
    args = sys.argv[1:]

    if not args or args[0] in HELP_FLAGS:
        _print_help()
        return

    command = args[0]
    command_args = args[1:]

    if command == COMMAND_SERVE:
        _run_serve(command_args)
    elif command == COMMAND_DEV_TUNNEL:
        _run_dev_tunnel(command_args)
    elif command == COMMAND_WATCH_FILES:
        run_watch_files(command_args)
    elif command == COMMAND_CONNECTIONS:
        run_connections(command_args)
    elif command == COMMAND_STUDIO:
        _run_studio(command_args)
    elif command == COMMAND_VERSION:
        _show_version()
    else:
        print(f'{ERROR_PREFIX} Unknown command: {command}', file=sys.stderr)
        print(file=sys.stderr)
        _print_help(file=sys.stderr)
        sys.exit(EXIT_ERROR)


def _print_help(*, file: TextIO | None = None) -> None:
    """Print CLI help message."""
    stream = file or sys.stdout
    print('MAIVN SDK Command Line Interface', file=stream)
    print(file=stream)
    print('Usage: maivn <command> [options]', file=stream)
    print(file=stream)
    print('Commands:', file=stream)
    for command, description in COMMAND_HELP:
        print(f'  {command:<9} {description}', file=stream)
    print(file=stream)
    print('Run "maivn serve --help" for serving options.', file=stream)
    print('Run "maivn dev-tunnel --help" for local webhook receiver options.', file=stream)
    print('Run "maivn watch-files --help" for explicit local file observation.', file=stream)
    print('Run "maivn studio --help" for studio-specific options.', file=stream)


def _print_serve_help(*, file: TextIO | None = None) -> None:
    """Print serving usage, options, and examples."""
    stream = file or sys.stdout
    print('Usage: maivn serve <module|path.py>[:attribute] [options]', file=stream)
    print(file=stream)
    print('Announce a locally running agent to the platform and keep it announced.', file=stream)
    print('The agent runs here, in this process. The platform learns its identity', file=stream)
    print('and a fingerprint of its definition, never the definition itself.', file=stream)
    print(file=stream)
    print('Options:', file=stream)
    print(f'  --project-id ID     Project to announce into (or {PROJECT_ID_ENV_VAR})', file=stream)
    print('  --agent-id ID       Agent id to announce as (default: agent ID or name)', file=stream)
    print('  --version N         Version this process serves (default: agent version)', file=stream)
    print('  --transport NAME    worker or webhook (default: worker)', file=stream)
    print('  --endpoint-url URL  HTTPS endpoint, required by --transport webhook', file=stream)
    print(file=stream)
    print('Examples:', file=stream)
    print('  maivn serve app.py', file=stream)
    print('  maivn serve myapp.agents:triage --version 3', file=stream)


def _run_dev_tunnel(args: list[str]) -> None:
    """Expose explicitly selected signed receiver paths until Ctrl-C."""
    parser = argparse.ArgumentParser(
        prog='maivn dev-tunnel',
        description='Open a free temporary HTTPS tunnel for local webhook receivers.',
        epilog=(
            f'Only selected receiver POSTs reach {LOCAL_BASE_URL}. '
            'Signatures remain required. Keep agents serving locally. '
            'Press Ctrl-C to close the tunnel and its local proxy.'
        ),
    )
    parser.add_argument(
        '--connection-id',
        action='append',
        help='Connection to expose; repeat for each connection.',
    )
    parser.add_argument(
        '--install',
        action='store_true',
        help='Install the verified free client without opening a tunnel.',
    )
    if not args or any(item in HELP_FLAGS for item in args):
        parser.print_help()
        return
    parsed = parser.parse_args(args)
    if parsed.install and parsed.connection_id:
        parser.error('Install separately, then start with --connection-id.')
    if not parsed.install and not parsed.connection_id:
        parser.error('Choose --install or at least one --connection-id.')
    connection_ids = cast('list[str]', parsed.connection_id)
    try:
        if parsed.install:
            print(f'{INFO_PREFIX} Installing the verified official cloudflared client...')
            path = install_cloudflared()
            print(f'{INFO_PREFIX} Ready: {path}. No tunnel is open.')
            print('Start with: maivn dev-tunnel --connection-id YOUR_CONNECTION_ID')
            return
        # Verify the executable before opening any listener.
        require_cloudflared()
        with ReceiverProxy(connection_ids) as proxy, CloudflaredTunnel(proxy.port) as tunnel:
            print(f'{INFO_PREFIX} Temporary HTTPS receiver URLs (Cloudflare Quick Tunnel):')
            for path in sorted(proxy.paths):
                print(f'  {tunnel.public_url}{path}')
            print(f'{INFO_PREFIX} Only these signed POST routes reach the local relay.')
            print(
                f'{INFO_PREFIX} Keep maivn serve running locally. Press Ctrl-C to close the tunnel.'
            )
            tunnel.wait()
    except KeyboardInterrupt:
        print(f'{INFO_PREFIX} Tunnel and local receiver proxy stopped.', file=sys.stderr)
        sys.exit(EXIT_SIGINT)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'{ERROR_PREFIX} {exc}', file=sys.stderr)
        sys.exit(EXIT_ERROR)


def _run_serve(args: list[str]) -> None:
    """Load the developer's agent and serve it until the process is stopped."""
    if not args or args[0] in HELP_FLAGS:
        _print_serve_help()
        return
    request = _parse_serve_request(args)
    if request is None:
        print(file=sys.stderr)
        _print_serve_help(file=sys.stderr)
        sys.exit(EXIT_ERROR)
    try:
        agent = _load_agent(request.target)
    except (ImportError, OSError, ValueError) as exc:
        print(f'{ERROR_PREFIX} {exc}', file=sys.stderr)
        sys.exit(EXIT_ERROR)
    _serve_agent(agent, request)


def _parse_serve_request(args: list[str]) -> ServeRequest | None:
    """Validate serve arguments, reporting the first unusable one."""
    flags = _parse_serve_flags(args[1:])
    if flags is None:
        return None
    raw_version = flags.get('--version')
    if raw_version is not None and (not raw_version.isdigit() or int(raw_version) < 1):
        print(f'{ERROR_PREFIX} --version must be a positive whole number', file=sys.stderr)
        return None
    transport = flags.get('--transport', 'worker')
    if transport not in SERVING_TRANSPORTS:
        supported = ', '.join(SERVING_TRANSPORTS)
        print(f'{ERROR_PREFIX} --transport must be one of: {supported}', file=sys.stderr)
        return None
    return ServeRequest(
        target=args[0],
        project_id=flags.get('--project-id'),
        agent_id=flags.get('--agent-id'),
        version=int(raw_version) if raw_version is not None else None,
        transport=transport,
        endpoint_url=flags.get('--endpoint-url'),
    )


def _parse_serve_flags(args: list[str]) -> dict[str, str] | None:
    """Collect ``--name value`` and ``--name=value`` options, or report the bad one."""
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        token = args[index]
        name, separator, inline = token.partition('=')
        if name not in SERVE_FLAGS:
            print(f'{ERROR_PREFIX} Unknown serve option: {token}', file=sys.stderr)
            return None
        if separator:
            values[name] = inline
            index += 1
            continue
        if index + 1 >= len(args):
            print(f'{ERROR_PREFIX} Missing value for {name}', file=sys.stderr)
            return None
        values[name] = args[index + 1]
        index += 2
    return values


def _split_agent_target(target: str) -> tuple[str, str]:
    r"""Split ``module[:attribute]`` without eating a Windows drive letter.

    Splitting on the FIRST colon turned ``C:\work\app.py`` into module ``C``
    and made every absolute path on Windows unloadable. The last colon is the
    separator, and only when what follows it could name a Python attribute.
    """
    reference, separator, attribute = target.rpartition(':')
    if separator and reference and attribute.isidentifier():
        return reference, attribute
    return target, ''


def _load_agent(target: str) -> Agent:
    """Load ``module[:attribute]`` and return the agent to serve."""
    reference, attribute = _split_agent_target(target)
    module = _load_agent_module(reference)
    members = {
        name: value
        for name, value in cast('dict[str, object]', vars(module)).items()
        if isinstance(value, Agent)
    }
    if attribute:
        found = members.get(attribute)
        if found is None:
            message = f'{reference} has no agent named {attribute}'
            raise ValueError(message)
        return found
    if not members:
        message = f'{reference} defines no maivn Agent to serve'
        raise ValueError(message)
    if len(members) > 1:
        names = ', '.join(sorted(members))
        message = f'{reference} defines several agents ({names}); pick one with {reference}:<name>'
        raise ValueError(message)
    return next(iter(members.values()))


def _load_agent_module(reference: str) -> ModuleType:
    """Import an agent module by dotted name, or execute it by file path."""
    path = Path(reference)
    if path.suffix != '.py' and not path.exists():
        return import_module(reference)
    resolved = path.resolve()
    if not resolved.is_file():
        message = f'no such agent module file: {reference}'
        raise FileNotFoundError(message)
    spec = spec_from_file_location(resolved.stem, resolved)
    if spec is None or spec.loader is None:
        message = f'cannot load an agent module from {reference}'
        raise ImportError(message)
    module = module_from_spec(spec)
    # What `python app.py` already does: the file's own directory leads the
    # search path, so the developer's sibling imports resolve the same way when
    # the file is run through this CLI instead.
    sys.path.insert(0, str(resolved.parent))
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _serve_agent(agent: Agent, request: ServeRequest) -> None:
    """Run the serving loop, reporting liveness and translating failures."""
    try:
        asyncio.run(
            agent.serve(
                project_id=request.project_id,
                agent_id=request.agent_id,
                version=request.version,
                transport=request.transport,
                endpoint_url=request.endpoint_url,
                listener=_report_serving_event,
            )
        )
    except KeyboardInterrupt:
        print(f'{INFO_PREFIX} Stopped serving {agent.name}.', file=sys.stderr)
        sys.exit(EXIT_SIGINT)
    except MaivnHTTPError as exc:
        print(f'{ERROR_PREFIX} {exc}', file=sys.stderr)
        print(
            f'{INFO_PREFIX} The platform refused this process. Check that MAIVN_API_KEY is '
            f'set to a key allowed to serve this project.',
            file=sys.stderr,
        )
        sys.exit(EXIT_ERROR)
    except (MaivnSDKError, ValueError) as exc:
        print(f'{ERROR_PREFIX} {exc}', file=sys.stderr)
        sys.exit(EXIT_ERROR)


def _report_serving_event(event: ServingEvent) -> None:
    """Print one liveness line. Never the API key, never anything from the definition."""
    label = f'{event.agent_id} v{event.version}'
    if event.kind in {'announced', 're_announced'}:
        verb = 'Announced' if event.kind == 'announced' else 'Re-announced'
        print(
            f'{INFO_PREFIX} {verb} {label} as instance {event.instance_id} '
            f'(process {event.process_id}); heartbeat every '
            f'{HEARTBEAT_INTERVAL_SECONDS:.0f}s. Press Ctrl-C to stop.'
        )
    elif event.kind == 'heartbeat':
        print(f'{INFO_PREFIX} Heartbeat accepted for {label}.')
    elif event.kind == 'retrying':
        print(f'{INFO_PREFIX} Platform unreachable for {label}: {event.detail}', file=sys.stderr)
    elif event.kind == 'unreachable':
        print(
            f'{ERROR_PREFIX} Missed a heartbeat for {label}: {event.detail}. '
            f'Still serving; retrying on the next beat.',
            file=sys.stderr,
        )
    else:
        detail = f' ({event.detail})' if event.detail else ''
        print(f'{INFO_PREFIX} Stopped serving {label}{detail}.')


def _run_studio(extra_args: list[str]) -> None:
    """Launch MAIVN Studio, offering to install the companion if it is missing."""
    if find_spec(STUDIO_MODULE) is None and not _ensure_studio_installed():
        sys.exit(EXIT_ERROR)
    _launch_studio(extra_args)


def _is_interactive() -> bool:
    """Return True when both stdin and stdout are attached to a terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_studio_installed() -> bool:
    """Offer to install the missing Studio companion.

    Returns True when Studio has just been installed and is ready to launch;
    False when the caller should abort. In a non-interactive shell, prints the
    install hint and returns False rather than blocking on a prompt.
    """
    if not _is_interactive():
        _print_studio_missing_hint()
        return False

    try:
        answer = input(f'{INFO_PREFIX} MAIVN Studio is not installed. Install it now? [y/N]: ')
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        answer = ''

    if answer.strip().lower() not in {'y', 'yes'}:
        print(
            f'{INFO_PREFIX} To install it later: uv pip install {STUDIO_PACKAGE}',
            file=sys.stderr,
        )
        return False

    print(f'{INFO_PREFIX} Installing {STUDIO_PACKAGE}...', file=sys.stderr)
    install: subprocess.CompletedProcess[bytes] = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', STUDIO_PACKAGE], check=False
    )
    if install.returncode != 0:
        print(
            f'{ERROR_PREFIX} Automatic install failed. Install it manually:',
            file=sys.stderr,
        )
        print(f'       uv pip install {STUDIO_PACKAGE}', file=sys.stderr)
        return False
    return True


def _launch_studio(extra_args: list[str]) -> None:
    """Delegate to Studio as ``sys.executable -m maivn_studio.main``.

    The module launch guarantees the Studio that runs is the one installed
    alongside THIS ``maivn`` CLI — the environment the user's project
    selected. There is deliberately no ``maivn-studio`` console-script
    fallback: Studio no longer generates one (the Windows .exe wrapper
    locks itself while running and a stale one blocked sync and startup),
    and probing PATH let an unrelated globally-installed Studio shadow the
    project's own copy.
    """
    try:
        result: subprocess.CompletedProcess[bytes] = subprocess.run(
            [sys.executable, '-m', STUDIO_MODULE_MAIN, *extra_args],
            check=False,
        )
    except KeyboardInterrupt:
        sys.exit(EXIT_SIGINT)
    sys.exit(result.returncode)


def _print_studio_missing_hint() -> None:
    """Print the companion-install hint when Studio is unavailable."""
    print(
        f'{ERROR_PREFIX} Cannot launch MAIVN Studio: {STUDIO_MODULE} is not installed',
        file=sys.stderr,
    )
    print(f'{INFO_PREFIX} Make sure the Studio companion is installed:', file=sys.stderr)
    print(f'       uv pip install {STUDIO_PACKAGE}', file=sys.stderr)


def _show_version() -> None:
    """Show the MAIVN SDK version."""
    print(f'maivn {__version__}')


if __name__ == '__main__':
    main()
