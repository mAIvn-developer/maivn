"""Unit tests for SDK markdown skill authoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from maivn import (
    Skill,
    SkillChildNotFoundError,
    SkillCycleError,
    SkillFrontmatterError,
    SkillSet,
    parse_skill_markdown,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / 'fixtures' / 'skills' / 'renewal'


def test_parse_directory_skill_set_with_nested_markdown_tree() -> None:
    """Directory form parses a parent set, sibling skills, and nested child sets."""
    parsed = parse_skill_markdown(FIXTURE_ROOT)

    assert isinstance(parsed, SkillSet)
    assert parsed.name == 'Renewal Handling'
    assert parsed.description == (
        'The full procedure for at-risk contract renewals.\n\n'
        'Use this family when a renewal has blockers that need coordinated action.'
    )
    assert [child.name for child in parsed.children] == [
        'Gather Context',
        'Legal Escalation',
        'Send Summary',
    ]

    gather = parsed.children[0]
    legal = parsed.children[1]
    assert isinstance(gather, Skill)
    assert [step.action for step in gather.steps] == [
        'Collect owner and account context',
        'Record the renewal deadline and blocker summary',
    ]
    assert isinstance(legal, SkillSet)
    assert [child.name for child in legal.children] == ['Escalate to Legal']


def test_parse_rejects_malformed_frontmatter(tmp_path: Path) -> None:
    """Malformed frontmatter raises a typed parser error."""
    path = tmp_path / 'broken.md'
    path.write_text(
        '---\nskill Renewal Handling\ndescription: Missing a key separator.\nkind: skill\n---\n',
        encoding='utf-8',
    )

    with pytest.raises(SkillFrontmatterError, match='frontmatter'):
        parse_skill_markdown(path)


def test_parse_rejects_missing_children(tmp_path: Path) -> None:
    """A declared child path must exist relative to the parent file."""
    path = tmp_path / 'SET.md'
    path.write_text(
        '---\n'
        'skill: Missing Child\n'
        'description: Broken child reference.\n'
        'kind: set\n'
        'children:\n'
        '  - ./missing.md\n'
        '---\n',
        encoding='utf-8',
    )

    with pytest.raises(SkillChildNotFoundError, match=r'missing.md'):
        parse_skill_markdown(path)


def test_parse_rejects_recursive_child_cycles(tmp_path: Path) -> None:
    """A skill set tree cannot reference an ancestor."""
    parent = tmp_path / 'SET.md'
    child = tmp_path / 'child.md'
    parent.write_text(
        '---\nskill: Parent\ndescription: Parent set.\nkind: set\nchildren:\n  - ./child.md\n---\n',
        encoding='utf-8',
    )
    child.write_text(
        '---\nskill: Child\ndescription: Child set.\nkind: set\nchildren:\n  - ./SET.md\n---\n',
        encoding='utf-8',
    )

    with pytest.raises(SkillCycleError, match='cycle'):
        parse_skill_markdown(parent)
