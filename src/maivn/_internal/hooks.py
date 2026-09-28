"""Identity-preserving composition for developer execution hooks.

A tool call can be surrounded by hooks registered at three different levels -
the swarm, the agent, and the tool itself. Compiling them into one opaque
callable is enough to *run* them, but a run trace has to say which callback
fired, at which level, and whether it succeeded. These types keep each
callback individually addressable while still presenting the single
``before_execute`` / ``after_execute`` callable slot the tool metadata holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# MARK: Hook Bindings


@dataclass(frozen=True, slots=True)
class HookBinding:
    """One developer callback plus the scope level that registered it."""

    hook: Callable[..., object]
    source: str
    """``"tool"`` / ``"scope"`` / ``"swarm"`` - which level *defined* the hook."""
    target_type: str
    """``"tool"`` / ``"agent"`` / ``"swarm"`` - which card the firing attaches to."""
    target_name: str | None = None
    """Display name of that card."""

    @property
    def name(self) -> str:
        """Return the callback's display name, as a trace would show it."""
        raw = getattr(self.hook, '__name__', None)
        if isinstance(raw, str) and raw.strip():
            return raw
        return type(self.hook).__name__


@dataclass(frozen=True, slots=True)
class ComposedHook:
    """Run a chain of bindings in order, keeping each callback identifiable.

    Calling the composed hook directly runs every binding, which is what any
    caller holding only the ``before_execute`` slot expects. Callers that need
    per-callback reporting read :attr:`bindings` instead - see
    :func:`hook_bindings`.
    """

    bindings: tuple[HookBinding, ...]

    def __call__(self, payload: dict[str, object]) -> None:
        """Run every bound callback against one hook payload."""
        for binding in self.bindings:
            binding.hook(payload)


def compose_hooks(bindings: list[HookBinding]) -> Callable[..., object] | None:
    """Return the single callable that runs ``bindings``, or None when empty."""
    if not bindings:
        return None
    return ComposedHook(tuple(bindings))


def hook_bindings(
    hook: Callable[..., object] | None,
    *,
    target_name: str | None,
) -> tuple[HookBinding, ...]:
    """Return the individual callbacks behind one compiled hook slot.

    A bare callable never passed through scope composition is a hook the
    developer registered on the tool itself, so it reports as one tool-sourced
    binding against ``target_name``.
    """
    if hook is None:
        return ()
    if isinstance(hook, ComposedHook):
        return hook.bindings
    return (
        HookBinding(
            hook=hook,
            source='tool',
            target_type='tool',
            target_name=target_name,
        ),
    )


def safe_hook_error(error: Exception) -> str:
    """Return a hook failure descriptor that cannot disclose callback payloads."""
    return type(error).__name__


__all__ = [
    'ComposedHook',
    'HookBinding',
    'compose_hooks',
    'hook_bindings',
    'safe_hook_error',
]
