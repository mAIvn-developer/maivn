# pyright: strict
"""Contract checks for the public SDK onboarding documentation."""

from __future__ import annotations

from pathlib import Path

GETTING_STARTED = Path(__file__).parents[1] / "docs" / "guides" / "getting-started.md"
PUBLIC_README = Path(__file__).parents[1] / "README.md"


def test_getting_started_includes_copy_pasteable_environment_and_key_setup() -> None:
    content = GETTING_STARTED.read_text(encoding="utf-8")
    required_instructions = (
        "python -m venv .venv",
        r".venv\Scripts\Activate.ps1",
        "source .venv/bin/activate",
        "python -m pip install maivn",
        "uv venv --python 3.12",
        "uv pip install maivn",
        "https://developer.maivn.io/projects/current/api-keys",
    )

    missing = [instruction for instruction in required_instructions if instruction not in content]

    assert not missing, f"Getting Started is missing required onboarding instructions: {missing}"


def test_getting_started_examples_do_not_send_a_literal_placeholder_key() -> None:
    content = GETTING_STARTED.read_text(encoding="utf-8")

    assert "api_key='your-api-key'" not in content


def test_public_readme_links_to_key_creation_and_isolated_setup() -> None:
    content = PUBLIC_README.read_text(encoding="utf-8")
    required_instructions = (
        "python -m venv .venv",
        "uv venv --python 3.12",
        "https://developer.maivn.io/projects/current/api-keys",
    )

    missing = [instruction for instruction in required_instructions if instruction not in content]

    assert not missing, f"Public README is missing required onboarding instructions: {missing}"
    assert "api_key='your-api-key'" not in content
