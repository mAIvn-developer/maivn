from __future__ import annotations

from maivn_shared import DataDependency
from pydantic import BaseModel

from maivn import depends_on_private_data
from maivn._internal.core.services.dependency_collector import DependencyCollector


@depends_on_private_data("alpha", "alpha")
def _tool(alpha: str) -> str:
    return alpha


class InnerModel(BaseModel):
    value: int


class OuterModel(BaseModel):
    inner: InnerModel


class PendingModel(BaseModel):
    value: int


PendingModel.__maivn_pending_deps__ = [DataDependency(arg_name="beta", data_key="beta")]  # type: ignore[attr-defined]


def test_dependency_collector_collects_from_function_and_model() -> None:
    collector = DependencyCollector()

    deps = collector.collect_all(_tool)
    assert any(isinstance(dep, DataDependency) and dep.data_key == "alpha" for dep in deps)

    InnerModel._dependencies = [DataDependency(arg_name="inner", data_key="inner")]  # type: ignore[attr-defined]
    model_deps = collector.collect_all(OuterModel)
    assert any(isinstance(dep, DataDependency) and dep.data_key == "inner" for dep in model_deps)


def test_dependency_collector_includes_pending_deps() -> None:
    collector = DependencyCollector()

    deps = collector.collect_all(PendingModel)
    assert any(isinstance(dep, DataDependency) and dep.data_key == "beta" for dep in deps)
