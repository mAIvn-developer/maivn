"""Pure markdown parser for SDK skill and skill-set authoring."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, cast

from maivn._internal.skills.models import Skill, SkillSet, SkillStep

_FRONTMATTER_DELIMITER = '---'
_SET_FILENAMES = ('SET.md', '_set.md')


class SkillMarkdownError(ValueError):
    """Base error for malformed SDK skill markdown."""


class SkillFrontmatterError(SkillMarkdownError):
    """Raised when a skill markdown file has invalid frontmatter."""


class SkillChildNotFoundError(SkillMarkdownError):
    """Raised when a skill-set child path cannot be resolved."""


class SkillCycleError(SkillMarkdownError):
    """Raised when skill-set child references contain a cycle."""


class _MarkdownRecord(NamedTuple):
    """Parsed markdown fields before recursive child resolution."""

    name: str
    description: str
    kind: str
    child_refs: tuple[str, ...]
    steps: tuple[SkillStep, ...]


def parse_skill_markdown(path: str | Path) -> Skill | SkillSet:
    """Parse a skill markdown file or directory into an SDK skill tree."""
    return _parse_path(Path(path), stack=())


def _parse_path(path: Path, *, stack: tuple[Path, ...]) -> Skill | SkillSet:
    resolved = _resolve_existing(path)
    if resolved in stack:
        message = f'skill markdown cycle detected at {resolved}'
        raise SkillCycleError(message)
    if resolved.is_dir():
        return _parse_directory(resolved, stack=(*stack, resolved))
    return _parse_file(resolved, stack=(*stack, resolved))


def _parse_directory(path: Path, *, stack: tuple[Path, ...]) -> SkillSet:
    parent_path = _directory_parent_path(path)
    record = _read_markdown_record(parent_path)
    if record.kind != 'set':
        message = f'directory parent frontmatter kind must be set: {parent_path}'
        raise SkillFrontmatterError(message)
    child_paths = _directory_child_paths(path, parent_path=parent_path, refs=record.child_refs)
    children = tuple(_parse_path(child_path, stack=stack) for child_path in child_paths)
    return SkillSet(name=record.name, description=record.description, children=children)


def _parse_file(path: Path, *, stack: tuple[Path, ...]) -> Skill | SkillSet:
    record = _read_markdown_record(path)
    if record.kind == 'skill':
        if not record.steps:
            message = f'skill markdown must define at least one step: {path}'
            raise SkillFrontmatterError(message)
        return Skill(name=record.name, description=record.description, steps=record.steps)
    if record.kind == 'set':
        child_paths = _child_paths(path.parent, refs=record.child_refs)
        children = tuple(_parse_path(child_path, stack=stack) for child_path in child_paths)
        return SkillSet(name=record.name, description=record.description, children=children)
    message = f'frontmatter kind must be skill or set: {path}'
    raise SkillFrontmatterError(message)


def _read_markdown_record(path: Path) -> _MarkdownRecord:
    text = path.read_text(encoding='utf-8')
    frontmatter, body = _split_frontmatter(text, path=path)
    fields = _parse_frontmatter(frontmatter, path=path)
    kind = _required_string(fields, 'kind', path=path)
    description = _description(
        frontmatter_description=_required_string(fields, 'description', path=path),
        body=body,
    )
    return _MarkdownRecord(
        name=_required_string(fields, 'skill', path=path),
        description=description,
        kind=kind,
        child_refs=tuple(cast('list[str]', fields.get('children', []))),
        steps=_parse_steps(body),
    )


def _split_frontmatter(text: str, *, path: Path) -> tuple[list[str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        message = f'skill markdown frontmatter is required: {path}'
        raise SkillFrontmatterError(message)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_DELIMITER:
            return lines[1:index], '\n'.join(lines[index + 1 :])
    message = f'skill markdown frontmatter is not closed: {path}'
    raise SkillFrontmatterError(message)


def _parse_frontmatter(lines: list[str], *, path: Path) -> dict[str, object]:
    fields: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if ':' not in line:
            message = f'invalid frontmatter line in {path}: {line}'
            raise SkillFrontmatterError(message)
        key, raw_value = line.split(':', 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            message = f'invalid frontmatter key in {path}: {line}'
            raise SkillFrontmatterError(message)
        if value:
            fields[key] = value
            index += 1
            continue
        if key != 'children':
            message = f'frontmatter field {key} needs a scalar value in {path}'
            raise SkillFrontmatterError(message)
        child_refs, index = _parse_children(lines, start=index + 1, path=path)
        fields[key] = child_refs
    return fields


def _parse_children(lines: list[str], *, start: int, path: Path) -> tuple[list[str], int]:
    children: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        if not stripped.startswith('- '):
            break
        child = stripped[2:].strip()
        if not child:
            message = f'empty child reference in {path}'
            raise SkillFrontmatterError(message)
        children.append(child)
        index += 1
    return children, index


def _description(*, frontmatter_description: str, body: str) -> str:
    body_description = '\n'.join(_body_description_lines(body))
    if not body_description:
        return frontmatter_description
    return f'{frontmatter_description}\n\n{body_description}'


def _body_description_lines(body: str) -> list[str]:
    lines: list[str] = []
    in_steps = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower() == '## steps':
            in_steps = True
            continue
        if stripped.startswith('## '):
            in_steps = False
            continue
        if stripped.startswith('#') or in_steps:
            continue
        lines.append(stripped)
    return lines


def _parse_steps(body: str) -> tuple[SkillStep, ...]:
    steps: list[SkillStep] = []
    in_steps = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower() == '## steps':
            in_steps = True
            continue
        if in_steps and stripped.startswith('## '):
            break
        if not in_steps or not stripped:
            continue
        parsed = _parse_step_line(stripped)
        if parsed is not None:
            steps.append(parsed)
    return tuple(steps)


def _parse_step_line(line: str) -> SkillStep | None:
    number, separator, action = line.partition('.')
    if not separator or not number.isdecimal():
        return None
    stripped_action = action.strip()
    if not stripped_action:
        return None
    return SkillStep(index=int(number), action=stripped_action)


def _directory_parent_path(path: Path) -> Path:
    for filename in _SET_FILENAMES:
        candidate = path / filename
        if candidate.is_file():
            return candidate.resolve()
    message = f'skill-set directory needs SET.md or _set.md: {path}'
    raise SkillFrontmatterError(message)


def _directory_child_paths(
    path: Path,
    *,
    parent_path: Path,
    refs: tuple[str, ...],
) -> tuple[Path, ...]:
    explicit = list(_child_paths(path, refs=refs))
    siblings = sorted(
        (
            candidate.resolve()
            for candidate in path.glob('*.md')
            if candidate.resolve() != parent_path
        ),
        key=lambda item: item.name.lower(),
    )
    return tuple(_dedupe_paths([*explicit, *siblings]))


def _child_paths(parent: Path, *, refs: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(_resolve_existing(parent / ref) for ref in refs)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _resolve_existing(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists():
        return resolved
    message = f'skill markdown child path does not exist: {path}'
    raise SkillChildNotFoundError(message)


def _required_string(fields: dict[str, object], key: str, *, path: Path) -> str:
    value = fields.get(key)
    if isinstance(value, str) and value:
        return value
    message = f'skill markdown frontmatter field {key} is required: {path}'
    raise SkillFrontmatterError(message)


__all__ = [
    'SkillChildNotFoundError',
    'SkillCycleError',
    'SkillFrontmatterError',
    'SkillMarkdownError',
    'parse_skill_markdown',
]
