"""Characterization tests for the SDK skill markdown parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from maivn import (
    Skill,
    SkillChildNotFoundError,
    SkillCycleError,
    SkillFrontmatterError,
    SkillMarkdownError,
    SkillSet,
    SkillStep,
    parse_skill_markdown,
)

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding='utf-8')
    return path


def _skill_text(name: str, *, body: str = '## Steps\n1. Do it\n') -> str:
    return f'---\nskill: {name}\ndescription: {name} description.\nkind: skill\n---\n{body}'


def _set_text(name: str, children: tuple[str, ...] = ()) -> str:
    lines = [f'---\nskill: {name}\ndescription: {name} set.\nkind: set\n']
    if children:
        lines.append('children:\n')
        lines.extend(f'  - {child}\n' for child in children)
    lines.append('---\n')
    return ''.join(lines)


def _error_text(path: Path, error: type[SkillMarkdownError]) -> str:
    with pytest.raises(error) as raised:
        _ = parse_skill_markdown(path)
    return str(raised.value)


def test_skill_file_parses_description_sections_and_numbered_steps(tmp_path: Path) -> None:
    """Body prose joins the description; only numbered lines under Steps become steps."""
    path = _write(
        tmp_path / 'skill.md',
        '---\n'
        '\n'
        'skill: Close Renewal\n'
        'description: Close an at-risk renewal.\n'
        'kind: skill\n'
        '---\n'
        '# Close Renewal\n'
        'Use when the deadline is near.\n'
        '\n'
        '## Steps\n'
        'Intro line that is not a step.\n'
        '1. Confirm the owner\n'
        '2.   \n'
        'a. Not numbered\n'
        'No separator here\n'
        '3. Send the summary\n'
        '## Notes\n'
        'Keep the customer informed.\n'
        '4. Not a step after Steps ends\n',
    )

    parsed = parse_skill_markdown(str(path))

    assert parsed == Skill(
        name='Close Renewal',
        description=(
            'Close an at-risk renewal.\n\n'
            'Use when the deadline is near.\n'
            'Keep the customer informed.\n'
            '4. Not a step after Steps ends'
        ),
        steps=(
            SkillStep(index=1, action='Confirm the owner'),
            SkillStep(index=3, action='Send the summary'),
        ),
    )


def test_skill_without_body_prose_keeps_frontmatter_description(tmp_path: Path) -> None:
    """A body made only of headings and steps leaves the frontmatter description alone."""
    path = _write(tmp_path / 'plain.md', _skill_text('Plain', body='# Heading\n## Steps\n1. Go\n'))

    parsed = parse_skill_markdown(path)

    assert parsed == Skill(
        name='Plain',
        description='Plain description.',
        steps=(SkillStep(index=1, action='Go'),),
    )


def test_set_file_resolves_children_relative_to_its_directory(tmp_path: Path) -> None:
    """A set file lists children in declared order, including nested directory sets."""
    nested = tmp_path / 'nested'
    nested.mkdir()
    _ = _write(nested / '_set.md', _set_text('Nested'))
    _ = _write(nested / 'inner.md', _skill_text('Inner'))
    _ = _write(tmp_path / 'first.md', _skill_text('First'))
    parent = _write(
        tmp_path / 'root.md',
        '---\n'
        'skill: Root\n'
        'description: Root set.\n'
        'kind: set\n'
        'children:\n'
        '\n'
        '  - nested\n'
        '  - ./first.md\n'
        'extra: ignored\n'
        '---\n'
        'Root prose.\n',
    )

    parsed = parse_skill_markdown(parent)

    inner = Skill(
        name='Inner', description='Inner description.', steps=(SkillStep(index=1, action='Do it'),)
    )
    first = Skill(
        name='First', description='First description.', steps=(SkillStep(index=1, action='Do it'),)
    )
    assert parsed == SkillSet(
        name='Root',
        description='Root set.\n\nRoot prose.',
        children=(SkillSet(name='Nested', description='Nested set.', children=(inner,)), first),
    )


def test_directory_orders_explicit_children_then_sorted_siblings_once(tmp_path: Path) -> None:
    """Explicit children come first, remaining siblings follow case-insensitively, no repeats."""
    _ = _write(tmp_path / 'SET.md', _set_text('Family', ('zeta.md',)))
    _ = _write(tmp_path / 'zeta.md', _skill_text('Zeta'))
    _ = _write(tmp_path / 'Beta.md', _skill_text('Beta'))
    _ = _write(tmp_path / 'alpha.md', _skill_text('Alpha'))

    parsed = parse_skill_markdown(tmp_path)

    assert isinstance(parsed, SkillSet)
    assert parsed.name == 'Family'
    assert [child.name for child in parsed.children] == ['Zeta', 'Alpha', 'Beta']


def test_directory_without_set_file_is_rejected(tmp_path: Path) -> None:
    """A directory needs SET.md or _set.md to describe the family."""
    _ = _write(tmp_path / 'only.md', _skill_text('Only'))

    message = _error_text(tmp_path, SkillFrontmatterError)

    assert message == f'skill-set directory needs SET.md or _set.md: {tmp_path.resolve()}'


def test_directory_parent_must_be_a_set(tmp_path: Path) -> None:
    """A directory's SET.md must declare kind set."""
    set_path = _write(tmp_path / 'SET.md', _skill_text('Wrong'))

    message = _error_text(tmp_path, SkillFrontmatterError)

    assert message == f'directory parent frontmatter kind must be set: {set_path.resolve()}'


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('', 'skill markdown frontmatter is required: {path}'),
        ('skill: x\n', 'skill markdown frontmatter is required: {path}'),
        ('---\nskill: x\n', 'skill markdown frontmatter is not closed: {path}'),
        ('---\n: value\n---\n', 'invalid frontmatter key in {path}: : value'),
        ('---\nno separator\n---\n', 'invalid frontmatter line in {path}: no separator'),
        ('---\nskill:\n---\n', 'frontmatter field skill needs a scalar value in {path}'),
        (
            '---\nskill: x\ndescription: d\n---\n',
            'skill markdown frontmatter field kind is required: {path}',
        ),
        (
            '---\nkind: skill\nskill: x\n---\n',
            'skill markdown frontmatter field description is required: {path}',
        ),
        (
            '---\nkind: skill\ndescription: d\n---\n',
            'skill markdown frontmatter field skill is required: {path}',
        ),
        (
            '---\nskill: x\ndescription: d\nkind: workflow\n---\n',
            'frontmatter kind must be skill or set: {path}',
        ),
        (
            '---\nskill: x\ndescription: d\nkind: skill\n---\nProse only.\n',
            'skill markdown must define at least one step: {path}',
        ),
    ],
)
def test_malformed_skill_files_raise_exact_errors(
    tmp_path: Path,
    text: str,
    expected: str,
) -> None:
    """Each malformed shape raises a SkillFrontmatterError naming the resolved file."""
    path = _write(tmp_path / 'bad.md', text)

    message = _error_text(path, SkillFrontmatterError)

    assert message == expected.format(path=path.resolve())


def test_missing_root_path_raises_child_not_found(tmp_path: Path) -> None:
    """A missing path raises SkillChildNotFoundError naming the path as given."""
    missing = tmp_path / 'absent.md'

    message = _error_text(missing, SkillChildNotFoundError)

    assert message == f'skill markdown child path does not exist: {missing}'


def test_self_referencing_set_raises_cycle_error(tmp_path: Path) -> None:
    """A set that lists itself is a cycle at its own resolved path."""
    path = _write(tmp_path / 'loop.md', _set_text('Loop', ('loop.md',)))

    message = _error_text(path, SkillCycleError)

    assert message == f'skill markdown cycle detected at {path.resolve()}'


def test_markdown_errors_share_the_value_error_base() -> None:
    """Every parser error is a SkillMarkdownError and a ValueError."""
    for error in (SkillFrontmatterError, SkillChildNotFoundError, SkillCycleError):
        assert issubclass(error, SkillMarkdownError)
    assert issubclass(SkillMarkdownError, ValueError)
