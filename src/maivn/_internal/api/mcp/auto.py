"""Auto-setup instructions for local stdio MCP servers."""

from __future__ import annotations

import shutil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# MARK: MCPAutoSetup


class MCPAutoSetup(BaseModel):
    """Auto-setup instructions for local stdio MCP servers.

    This class provides configuration for automatically setting up MCP servers
    using package managers like uvx. It handles command resolution and
    environment configuration.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: Literal["uvx"] = Field(
        default="uvx",
        description="Installer used to run the MCP server",
    )
    package: str = Field(
        ...,
        description="Package or entry name for the MCP server",
    )
    args: list[str] = Field(
        default_factory=list,
        description="Arguments forwarded to the MCP server",
    )
    env: dict[str, str] | None = Field(default=None, description="Environment overrides")
    working_dir: str | None = Field(default=None, description="Working directory override")
    uvx_command: str | None = Field(
        default=None,
        description="Override uvx binary name (defaults to 'uvx')",
    )

    # MARK: - Validators

    @field_validator("package")
    @classmethod
    def _validate_package(cls, value: str) -> str:
        if not value or not isinstance(value, str):
            raise ValueError("auto_setup.package must be a non-empty string")
        return value

    # MARK: - Public Methods

    def resolve_command(self) -> tuple[str, list[str]]:
        """Resolve the command and arguments for running the MCP server.

        Returns:
            Tuple of (command, arguments) for subprocess execution.

        Raises:
            ValueError: If provider is not supported or uvx is not on PATH.
        """
        if self.provider != "uvx":
            raise ValueError(f"auto_setup provider '{self.provider}' is not supported")

        command = self.uvx_command or "uvx"
        if shutil.which(command) is None:
            raise ValueError("auto_setup provider 'uvx' requires 'uvx' on PATH")

        return command, [self.package, *self.args]


__all__ = ["MCPAutoSetup"]
