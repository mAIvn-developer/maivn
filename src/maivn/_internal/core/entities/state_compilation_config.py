"""Configuration model for state compilation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .mixins import ConfigurableMixin

# MARK: StateCompilationConfig


class StateCompilationConfig(ConfigurableMixin):
    """Configuration options applied during state compilation."""

    # MARK: - Fields

    include_timeout: bool = Field(
        default=True,
        description="Whether to include the timeout value in the session execution config.",
    )
    base_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Static metadata that is merged into every compiled state.",
    )
