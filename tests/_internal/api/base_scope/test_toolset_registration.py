# pyright: strict
"""Tests for the ``@toolset`` / ``@toolify`` class-instance toolify pattern."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import cast

import pytest
from maivn_shared import DataDependency

from maivn import ToolOverride, toolify, toolset
from maivn._internal.api.agent import Agent
from maivn._internal.api.client import Client
from maivn._internal.core.entities.tools import MethodTool
from maivn._internal.utils.configuration import MaivnConfiguration, ServerConfiguration
from maivn._internal.utils.decorators import depends_on_tool
from maivn._internal.utils.toolset import (
    derive_default_prefix,
    get_toolify_options,
    get_toolset_options,
)

# MARK: - Helpers


def _make_agent() -> Agent:
    config = MaivnConfiguration(
        server=ServerConfiguration(
            base_url="http://example.com",
            mock_base_url="http://example.com",
        )
    )
    client = Client.from_configuration(api_key="key", configuration=config)
    return Agent(name="t", client=client)


# MARK: - Decorator unit tests


def test_toolify_bare_marker_attaches_default_options() -> None:
    @toolify
    def method(self: object) -> None:
        """A method."""
        _ = self
        return None

    opts = get_toolify_options(method)
    assert opts is not None
    assert opts.permissions is None
    assert opts.destructive is False
    assert opts.tags == ()


def test_toolify_parameterized_attaches_options() -> None:
    @toolify(name="custom", destructive=True, permissions="write", tags=["t1", "t2"])
    def method(self: object) -> None:
        """A method."""
        _ = self
        return None

    opts = get_toolify_options(method)
    assert opts is not None
    assert opts.name == "custom"
    assert opts.destructive is True
    assert opts.permissions == "write"
    assert opts.tags == ("t1", "t2")


def test_toolify_rejects_non_callable() -> None:
    # ``toolify`` only accepts callables; the cast lands the literal past the bound.
    target = cast("Callable[..., object]", cast(object, 123))
    with pytest.raises(TypeError):
        _ = toolify(target)


def test_toolset_bare_marker_attaches_default_options() -> None:
    @toolset
    class Plain:
        @toolify
        def m(self) -> None:
            """."""

    opts = get_toolset_options(Plain)
    assert opts is not None
    assert opts.prefix is None
    assert opts.require_marker is True


def test_toolset_parameterized_options() -> None:
    @toolset(prefix="x", tags=["alpha"], require_marker=False, metadata={"owner": "team"})
    class Configured:
        def m(self) -> None:
            """."""

    opts = get_toolset_options(Configured)
    assert opts is not None
    assert opts.prefix == "x"
    assert opts.tags == ("alpha",)
    assert opts.require_marker is False
    assert opts.metadata == {"owner": "team"}


def test_toolset_rejects_non_class() -> None:
    # ``toolset`` only accepts classes; the cast lands the lambda past the bound.
    target = cast("type[object]", cast(object, lambda: None))
    with pytest.raises(TypeError):
        _ = toolset(target)


def test_derive_default_prefix_snake_cases_class_names() -> None:
    assert derive_default_prefix(type("GitHubToolset", (), {})) == "git_hub_toolset"
    assert derive_default_prefix(type("SmtpSender", (), {})) == "smtp_sender"
    assert derive_default_prefix(type("widget", (), {})) == "widget"
    assert derive_default_prefix(type("OAuth2Provider", (), {})) == "oauth2_provider"
    assert derive_default_prefix(type("XMLParser2", (), {})) == "xml_parser2"


# MARK: - Discovery and registration


def test_add_toolset_registers_each_toolify_method() -> None:
    @toolset(prefix="widgets")
    class Widgets:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        @toolify
        def list_widgets(self) -> list[int]:
            """List widgets."""
            self.calls.append("list")
            return [1, 2, 3]

        @toolify(destructive=True)
        def delete_widget(self, widget_id: int) -> int:
            """Delete a widget by id."""
            self.calls.append("delete")
            return widget_id

    agent = _make_agent()
    instance = Widgets()
    registered = agent.add_toolset(instance)

    assert len(registered) == 2
    assert {t.name for t in registered} == {"WIDGETS_list_widgets", "WIDGETS_delete_widget"}
    assert all(isinstance(t, MethodTool) for t in registered)
    assert all(t.owner is instance for t in registered)

    # Destructive flag rides along through metadata.
    delete_tool = next(t for t in registered if t.name == "WIDGETS_delete_widget")
    assert delete_tool.metadata.get("destructive") is True

    # Bound method binding is preserved at execution time.
    list_tool = next(t for t in registered if t.name == "WIDGETS_list_widgets")
    assert list_tool.func() == [1, 2, 3]
    assert instance.calls == ["list"]


def test_add_toolset_skips_private_and_unmarked_methods_by_default() -> None:
    @toolset(prefix="ts")
    class TS:
        @toolify
        def included(self) -> None:
            """Included."""

        def excluded_public(self) -> None:
            """Public helper that should not become a tool."""

        def _private(self) -> None:
            """Private helper."""

    agent = _make_agent()
    registered = agent.add_toolset(TS())
    assert {t.name for t in registered} == {"TS_included"}


def test_add_toolset_require_marker_false_includes_all_public_methods() -> None:
    @toolset(prefix="loose", require_marker=False)
    class Loose:
        def alpha(self) -> int:
            """Alpha."""
            return 1

        def beta(self) -> int:
            """Beta."""
            return 2

        def _hidden(self) -> int:
            """Hidden."""
            return 3

    agent = _make_agent()
    registered = agent.add_toolset(Loose())
    assert {t.name for t in registered} == {"LOOSE_alpha", "LOOSE_beta"}


def test_add_toolset_default_prefix_from_class_name() -> None:
    @toolset
    class GitHubToolset:
        @toolify
        def get_user(self) -> dict[str, object]:
            """Get user."""
            return {}

    agent = _make_agent()
    registered = agent.add_toolset(GitHubToolset())
    assert registered[0].name == "GIT_HUB_TOOLSET_get_user"


def test_add_toolset_rejects_non_toolset_class() -> None:
    class Plain:
        def hello(self) -> None:
            """."""

    agent = _make_agent()
    with pytest.raises(TypeError, match="not a toolset"):
        _ = agent.add_toolset(Plain())


def test_add_toolset_raises_when_no_methods_marked() -> None:
    @toolset(prefix="empty")
    class Empty:
        def helper(self) -> None:
            """Helper."""

    agent = _make_agent()
    with pytest.raises(ValueError, match="no @toolify-marked methods"):
        _ = agent.add_toolset(Empty())


def test_add_toolset_detects_duplicate_tool_names() -> None:
    @toolset(prefix="dup")
    class Dup:
        @toolify(name="same")
        def a(self) -> None:
            """A."""

        @toolify(name="same")
        def b(self) -> None:
            """B."""

    agent = _make_agent()
    with pytest.raises(ValueError, match="duplicate tool name"):
        _ = agent.add_toolset(Dup())


def test_add_toolset_method_options_override_class_tags_and_metadata() -> None:
    @toolset(prefix="merged", tags=["base"], metadata={"owner": "team-a"})
    class Merged:
        @toolify(tags=["specific"], metadata={"feature": "x"})
        def go(self) -> None:
            """Go."""

    agent = _make_agent()
    registered = agent.add_toolset(Merged())
    tool = registered[0]
    # Tags from both levels merge, preserving order without duplicates.
    assert tool.tags == ["base", "specific"]
    # Metadata is shallow-merged with method values winning on conflicts.
    assert tool.metadata["owner"] == "team-a"
    assert tool.metadata["feature"] == "x"


def test_add_toolset_preserves_existing_dependency_decorator() -> None:
    @toolset(prefix="deps")
    class WithDeps:
        @toolify
        def source(self) -> int:
            """Source."""
            return 42

        @depends_on_tool("DEPS_source", arg_name="value")
        @toolify
        def consumer(self, value: int) -> int:
            """Consumer that depends on source."""
            return value + 1

    agent = _make_agent()
    registered = agent.add_toolset(WithDeps())
    consumer = next(t for t in registered if t.name == "DEPS_consumer")
    # Dependency rolls up via the existing collector path.
    assert consumer.dependencies, "consumer should carry a recorded dependency"


def test_add_toolset_works_with_async_methods() -> None:
    @toolset(prefix="async_ts")
    class AsyncTS:
        @toolify
        async def fetch(self) -> int:
            """Fetch value."""
            await asyncio.sleep(0)
            return 7

    agent = _make_agent()
    registered = agent.add_toolset(AsyncTS())
    assert len(registered) == 1
    # The tool's ``func`` runs the original async method; ``MethodTool.func`` is typed for
    # sync invocation, so the returned coroutine arrives as ``object`` to the test.
    raw = registered[0].func()
    coro = cast("Coroutine[object, object, int]", raw)
    assert asyncio.run(coro) == 7


def test_add_toolset_returns_distinct_method_tools_per_instance() -> None:
    @toolset(prefix="instance")
    class Inst:
        @toolify
        def ping(self) -> str:
            """Ping."""
            return "pong"

    agent = _make_agent()
    a = Inst()
    b = Inst()
    tools_a = agent.add_toolset(a)
    tools_b = agent.add_toolset(b)
    assert tools_a[0].tool_id != tools_b[0].tool_id
    assert tools_a[0].owner is a
    assert tools_b[0].owner is b


def test_method_tool_qualified_name_uses_class_dot_method() -> None:
    @toolset(prefix="audit")
    class AuditTS:
        @toolify
        def emit(self) -> None:
            """Emit."""

    agent = _make_agent()
    registered = agent.add_toolset(AuditTS())
    assert registered[0].qualified_name == "AuditTS.emit"


def test_add_toolset_skips_properties_classmethods_and_staticmethods() -> None:
    @toolset(prefix="exotic")
    class Exotic:
        @toolify
        def real_tool(self) -> int:
            """Real tool."""
            return 1

        @property
        def maybe_attribute(self) -> int:
            """Should be skipped."""
            return 0

        @classmethod
        def cls_helper(cls) -> int:
            """Should be skipped."""
            return 0

        @staticmethod
        def static_helper() -> int:
            """Should be skipped."""
            return 0

    agent = _make_agent()
    registered = agent.add_toolset(Exotic())
    assert {t.name for t in registered} == {"EXOTIC_real_tool"}


def test_add_toolset_tools_are_remembered_on_agent_tools_list() -> None:
    @toolset(prefix="visibility")
    class V:
        @toolify
        def one(self) -> None:
            """One."""

    agent = _make_agent()
    registered = agent.add_toolset(V())
    # Agent.tools should reflect the freshly-added method tool.
    assert any(t is registered[0] for t in agent.tools)


def test_add_toolset_method_tools_are_compiled() -> None:
    @toolset(prefix="compile")
    class V:
        @toolify
        def one(self) -> str:
            """One."""
            return "ok"

    agent = _make_agent()
    registered = agent.add_toolset(V())

    compiled = agent.compile_tools()

    assert any(t is registered[0] for t in compiled)


# MARK: - Filter kwargs


class _MixedToolsetType:
    """Sentinel for filter-test mixin classes (constructed inline)."""


_T_Mixed = type[_MixedToolsetType]


def _filtered_agent_with_mixed_toolset() -> tuple[Agent, type[object]]:
    from maivn import PermissionFlag, PermissionSet

    @toolset(prefix="mixed")
    class Mixed:
        @toolify(permissions=PermissionSet(PermissionFlag.READ))
        def list_items(self) -> list[int]:
            """List."""
            return []

        @toolify(permissions=PermissionSet(PermissionFlag.READ))
        def get_item(self) -> dict[str, object]:
            """Get."""
            return {}

        @toolify(permissions=PermissionSet(PermissionFlag.WRITE))
        def create_item(self) -> dict[str, object]:
            """Create."""
            return {}

        @toolify(permissions=PermissionSet(PermissionFlag.DELETE), destructive=True)
        def delete_item(self) -> None:
            """Delete."""

    return _make_agent(), Mixed


def _instantiate(cls: type[object]) -> object:
    """Construct an instance of a dynamically-typed toolset class for ``add_toolset``."""
    return cls()


def test_add_toolset_include_allowlists_by_method_name() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    registered = agent.add_toolset(_instantiate(mixed_cls), include=["create_item"])
    assert {t.name for t in registered} == {"MIXED_create_item"}


def test_add_toolset_exclude_denylists_by_method_name() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    registered = agent.add_toolset(_instantiate(mixed_cls), exclude=["delete_item", "create_item"])
    assert {t.name for t in registered} == {"MIXED_list_items", "MIXED_get_item"}


def test_add_toolset_include_tags_keeps_only_matching_tools() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    registered = agent.add_toolset(_instantiate(mixed_cls), include_tags=["read"])
    assert {t.name for t in registered} == {"MIXED_list_items", "MIXED_get_item"}


def test_add_toolset_exclude_tags_drops_destructive_methods() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    registered = agent.add_toolset(_instantiate(mixed_cls), exclude_tags=["destructive"])
    # delete_item carries destructive=True; everything else stays.
    assert {t.name for t in registered} == {
        "MIXED_list_items",
        "MIXED_get_item",
        "MIXED_create_item",
    }


def test_add_toolset_destructive_permissions_auto_tag_methods() -> None:
    """A method whose permission set is destructive (e.g. ADMIN) auto-tags."""
    from maivn import PermissionFlag, PermissionSet

    @toolset(prefix="auto")
    class Auto:
        @toolify(permissions=PermissionSet(PermissionFlag.ADMIN))  # admin counts as destructive
        def grant_role(self) -> None:
            """Grant."""

        @toolify(permissions=PermissionSet(PermissionFlag.READ))
        def list_roles(self) -> None:
            """List."""

    agent = _make_agent()
    registered = agent.add_toolset(Auto(), exclude_tags=["destructive"])
    assert {t.name for t in registered} == {"AUTO_list_roles"}


def test_add_toolset_filter_uses_toolify_name_override_for_include() -> None:
    @toolset(prefix="rn")
    class Rn:
        @toolify(name="renamed")
        def actual_method(self) -> None:
            """Renamed."""

    agent = _make_agent()
    registered = agent.add_toolset(Rn(), include=["renamed"])
    assert [t.name for t in registered] == ["RN_renamed"]


def test_add_toolset_filter_combines_include_and_exclude_tags() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    # Keep read-tagged methods AND drop anything tagged destructive.
    registered = agent.add_toolset(
        _instantiate(mixed_cls),
        include_tags=["read"],
        exclude_tags=["destructive"],
    )
    assert {t.name for t in registered} == {"MIXED_list_items", "MIXED_get_item"}


def test_add_toolset_filter_to_zero_raises_when_marker_required() -> None:
    agent, mixed_cls = _filtered_agent_with_mixed_toolset()
    with pytest.raises(ValueError, match="filtered out"):
        _ = agent.add_toolset(_instantiate(mixed_cls), include=["does_not_exist"])


def test_add_toolset_filter_to_zero_is_permitted_without_marker_requirement() -> None:
    @toolset(prefix="loose", require_marker=False)
    class Loose:
        @toolify
        def a(self) -> None:
            """A."""

    agent = _make_agent()
    # No matching methods, but require_marker=False permits an empty result.
    registered = agent.add_toolset(Loose(), include=["nothing"])
    assert registered == []


def test_add_toolset_filter_uses_explicit_toolify_tags() -> None:
    @toolset(prefix="tagged")
    class Tagged:
        @toolify(tags=["email"])
        def send(self) -> None:
            """Send."""

        @toolify(tags=["calendar"])
        def schedule(self) -> None:
            """Schedule."""

    agent = _make_agent()
    registered = agent.add_toolset(Tagged(), include_tags=["email"])
    assert {t.name for t in registered} == {"TAGGED_send"}


def test_add_toolset_filter_uses_toolset_level_tags() -> None:
    """Toolset-level tags apply to every method-tool produced."""

    @toolset(prefix="cat", tags=["analytics"])
    class Cat:
        @toolify
        def a(self) -> None:
            """A."""

        @toolify(tags=["billing"])
        def b(self) -> None:
            """B."""

    agent = _make_agent()
    # include_tags=["analytics"] should match every method because it's
    # at the toolset level.
    registered = agent.add_toolset(Cat(), include_tags=["analytics"])
    assert {t.name for t in registered} == {"CAT_a", "CAT_b"}


# MARK: - ToolOverride tests


def test_add_toolset_override_replaces_scalars_and_appends_collections() -> None:
    @toolset(prefix="ov")
    class OV:
        @toolify
        def search(self, query: str) -> list[dict[str, object]]:
            """Generic search description."""
            return [{"q": query}]

        @toolify
        def fetch(self, item_id: str) -> dict[str, object]:
            """Generic fetch description."""
            return {"id": item_id}

    agent = _make_agent()
    registered = agent.add_toolset(
        OV(),
        overrides={
            "search": ToolOverride(
                description="App-specific framing.",
                always_execute=True,
                tags=["triage"],
                metadata={"audit_zone": "high"},
                dependencies=[DataDependency(arg_name="id", data_key="user_id")],
            ),
        },
    )

    search = next(t for t in registered if t.name == "OV_search")
    fetch = next(t for t in registered if t.name == "OV_fetch")

    # Scalars replaced on the overridden tool.
    assert search.description == "App-specific framing."
    assert search.always_execute is True
    # Tags appended (was empty before).
    assert "triage" in search.tags
    # Metadata merged.
    assert search.metadata.get("audit_zone") == "high"
    # Dependencies appended.
    assert len(search.dependencies) == 1

    # Untouched method is unchanged.
    assert fetch.description == "Generic fetch description."
    assert fetch.always_execute is False
    assert not fetch.dependencies


def test_add_toolset_override_unknown_key_raises() -> None:
    @toolset(prefix="ov")
    class OV:
        @toolify
        def search(self, query: str) -> list[dict[str, object]]:
            """."""
            _ = query
            return []

    agent = _make_agent()
    with pytest.raises(ValueError, match="override keys"):
        _ = agent.add_toolset(OV(), overrides={"misspelled": ToolOverride(always_execute=True)})


def test_add_toolset_override_renames_tool() -> None:
    @toolset(prefix="ov")
    class OV:
        @toolify
        def search(self, query: str) -> list[dict[str, object]]:
            """."""
            _ = query
            return []

    agent = _make_agent()
    registered = agent.add_toolset(
        OV(),
        overrides={"search": ToolOverride(name="inbox_finder")},
    )
    # Override name replaces the resolved name (which would otherwise be OV_search).
    assert registered[0].name == "inbox_finder"


def test_add_tool_override_replaces_kwargs() -> None:
    def my_func(query: str) -> dict[str, object]:
        """Original description."""
        return {"q": query}

    agent = _make_agent()
    registered = agent.add_tool(
        my_func,
        override=ToolOverride(
            name="retargeted_search",
            description="Retargeted description.",
            always_execute=True,
            tags=["custom"],
            default_args={"query": "status"},
        ),
    )

    assert registered.name == "retargeted_search"
    assert registered.description == "Retargeted description."
    assert registered.always_execute is True
    assert "custom" in registered.tags
    assert registered.metadata["default_args"] == {"query": "status"}


_ = _T_Mixed  # silence unused private alias kept for documentation purposes
