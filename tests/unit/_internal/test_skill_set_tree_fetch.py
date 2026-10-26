"""A skill set fetched through the SDK arrives with its whole tree in ``children``."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any, TypeAlias

import httpx
import pytest
from pydantic import AnyUrl

from maivn import Client, ClientConfig, MaivnHTTPError, MaivnSDKError, Skill, SkillSet

JsonObject: TypeAlias = dict[str, Any]

# The documented bounds: at most eight member reads at once; a skill path holds 16 sets.
MAX_CONCURRENT_READS = 8
MAX_DEPTH = 16

_SCOPE: JsonObject = {
    'project_id': 'project-sdk',
    'organization_id': 'org-sdk',
    'sharing_scope': 'project',
}


def _skill(skill_id: str, *, set_id: str) -> JsonObject:
    return {
        'kind': 'skill',
        'memory_id': skill_id,
        'scope': _SCOPE,
        'name': f'Skill {skill_id}',
        'description': f'Does {skill_id}.',
        'steps': [{'index': 1, 'action': f'Run {skill_id}'}],
        'preconditions': {},
        'postconditions': {},
        'confidence': 1.0,
        'origin': 'user_defined',
        'status': 'active',
        'created_at': '2026-07-05T12:00:00Z',
        'updated_at': '2026-07-05T12:00:00Z',
        'set_id': set_id,
    }


def _set(set_id: str, *, parent: str | None = None, members: tuple[str, ...] = ()) -> JsonObject:
    record: JsonObject = {
        'kind': 'skill_set',
        'id': set_id,
        'scope': _SCOPE,
        'name': f'Set {set_id}',
        'description': f'Groups {set_id}.',
        'member_skill_ids': list(members),
    }
    if parent is not None:
        record['parent_set_id'] = parent
    return record


class _FakeApi:
    """Serves one skill-set index and the member skills, recording each request."""

    def __init__(self, index: JsonObject, skills: dict[str, JsonObject]) -> None:
        self.index = index
        self.skills = skills
        self.paths: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(f'{request.method} {request.url.path}')
        path = request.url.path
        if path.endswith('/index'):
            return httpx.Response(HTTPStatus.OK, json=self.index)
        if path.startswith('/v1/skills/'):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.001)
            self.in_flight -= 1
            skill = self.skills.get(path.rsplit('/', 1)[1])
            if skill is None:
                return httpx.Response(HTTPStatus.NOT_FOUND, json={'detail': 'unknown skill'})
            return httpx.Response(HTTPStatus.OK, json=skill)
        return httpx.Response(HTTPStatus.NOT_FOUND, json={'detail': 'unexpected request'})


def _client(api: _FakeApi) -> Client:
    return Client(
        config=ClientConfig(api_key='test-key', base_url=AnyUrl('http://testserver')),
        transport=httpx.MockTransport(api),
    )


def _two_level_api() -> _FakeApi:
    index = {
        'skill_set': _set('root', members=('s-root',)),
        'descendant_skill_sets': [
            _set('child-a', parent='root', members=('s-a1', 's-a2')),
            _set('grandchild', parent='child-a', members=('s-g',)),
            _set('child-b', parent='root'),
        ],
        'skills': [],
    }
    skills = {
        's-root': _skill('s-root', set_id='root'),
        's-a1': _skill('s-a1', set_id='child-a'),
        's-a2': _skill('s-a2', set_id='child-a'),
        's-g': _skill('s-g', set_id='grandchild'),
    }
    return _FakeApi(index, skills)


def _shape(node: Skill | SkillSet) -> object:
    if isinstance(node, Skill):
        return node.memory_id
    return {str(node.id): [_shape(child) for child in node.children]}


def test_get_returns_member_skills_and_two_levels_of_nested_sets() -> None:
    """Skills come first in member order, then child sets in the index's order."""
    api = _two_level_api()

    fetched = _client(api).skills.skill_sets.get('root')

    assert _shape(fetched) == {
        'root': [
            's-root',
            {'child-a': ['s-a1', 's-a2', {'grandchild': ['s-g']}]},
            {'child-b': []},
        ],
    }
    assert fetched.member_skill_ids == ('s-root',)
    assert fetched.parent_set_id is None
    child_a = fetched.children[1]
    assert isinstance(child_a, SkillSet)
    assert child_a.parent_set_id == 'root'
    assert child_a.name == 'Set child-a'
    first_skill = fetched.children[0]
    assert isinstance(first_skill, Skill)
    assert first_skill.steps[0].action == 'Run s-root'


def test_get_sends_one_index_read_and_one_read_per_member_skill() -> None:
    """The tree costs one index request plus one request per distinct member skill."""
    api = _two_level_api()

    _client(api).skills.skill_sets.get('root')

    assert api.paths[0] == 'GET /v1/skill-sets/root/index'
    assert sorted(api.paths[1:]) == [
        'GET /v1/skills/s-a1',
        'GET /v1/skills/s-a2',
        'GET /v1/skills/s-g',
        'GET /v1/skills/s-root',
    ]


def test_member_skill_reads_run_concurrently_but_bounded() -> None:
    """Member skills are fetched together, never more than eight at a time."""
    members = tuple(f's-{number}' for number in range(20))
    api = _FakeApi(
        {'skill_set': _set('root', members=members), 'descendant_skill_sets': [], 'skills': []},
        {member: _skill(member, set_id='root') for member in members},
    )

    fetched = asyncio.run(_client(api).skills.skill_sets.aget('root'))

    assert [_shape(child) for child in fetched.children] == list(members)
    assert 1 < api.max_in_flight <= MAX_CONCURRENT_READS


def test_a_skill_shared_by_two_sets_is_read_once() -> None:
    """A member listed under two sets costs one request and appears under both."""
    api = _FakeApi(
        {
            'skill_set': _set('root', members=('shared',)),
            'descendant_skill_sets': [_set('child', parent='root', members=('shared',))],
            'skills': [],
        },
        {'shared': _skill('shared', set_id='root')},
    )

    fetched = _client(api).skills.skill_sets.get('root')

    assert _shape(fetched) == {'root': ['shared', {'child': ['shared']}]}
    assert api.paths.count('GET /v1/skills/shared') == 1


def test_descendants_that_loop_back_are_not_followed_twice() -> None:
    """A corrupt parent cycle among descendants cannot recurse forever."""
    api = _FakeApi(
        {
            'skill_set': _set('root'),
            'descendant_skill_sets': [
                _set('loop-a', parent='root'),
                _set('loop-b', parent='loop-a'),
                _set('loop-a', parent='loop-b'),
            ],
            'skills': [],
        },
        {},
    )

    fetched = _client(api).skills.skill_sets.get('root')

    assert _shape(fetched) == {'root': [{'loop-a': [{'loop-b': []}]}]}


def test_a_tree_deeper_than_the_set_path_limit_is_refused() -> None:
    """Skill paths hold at most 16 sets, so a deeper tree is refused, not truncated."""
    chain = [_set('level-1', parent='root')]
    chain.extend(_set(f'level-{depth}', parent=f'level-{depth - 1}') for depth in range(2, 17))
    api = _FakeApi(
        {'skill_set': _set('root'), 'descendant_skill_sets': chain, 'skills': []},
        {},
    )

    with pytest.raises(MaivnSDKError, match=r'^skill set root nests deeper than 16 levels$'):
        _client(api).skills.skill_sets.get('root')


def test_a_tree_exactly_at_the_set_path_limit_is_returned() -> None:
    """Sixteen levels, root included, is the deepest tree a skill path can describe."""
    chain = [_set('level-1', parent='root')]
    chain.extend(_set(f'level-{depth}', parent=f'level-{depth - 1}') for depth in range(2, 16))
    api = _FakeApi(
        {'skill_set': _set('root'), 'descendant_skill_sets': chain, 'skills': []},
        {},
    )

    node: Skill | SkillSet = _client(api).skills.skill_sets.get('root')
    depth = 1
    while isinstance(node, SkillSet) and node.children:
        node = node.children[0]
        depth += 1

    assert depth == MAX_DEPTH


def test_a_deleted_member_skill_is_left_out_instead_of_failing_the_fetch() -> None:
    """Deleting a skill does not remove it from its sets, so a 404 member is stale, not fatal."""
    api = _FakeApi(
        {
            'skill_set': _set('root', members=('kept', 'deleted')),
            'descendant_skill_sets': [],
            'skills': [],
        },
        {'kept': _skill('kept', set_id='root')},
    )

    fetched = _client(api).skills.skill_sets.get('root')

    assert _shape(fetched) == {'root': ['kept']}
    assert fetched.member_skill_ids == ('kept', 'deleted')


def test_any_other_member_read_failure_still_raises() -> None:
    """Only a missing skill is skipped; a server error is not hidden."""

    class _Failing(_FakeApi):
        async def __call__(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == '/v1/skills/broken':
                return httpx.Response(HTTPStatus.INTERNAL_SERVER_ERROR, json={'detail': 'boom'})
            return await super().__call__(request)

    api = _Failing(
        {'skill_set': _set('root', members=('broken',)), 'descendant_skill_sets': [], 'skills': []},
        {},
    )

    with pytest.raises(MaivnHTTPError) as caught:
        _client(api).skills.skill_sets.get('root')
    assert caught.value.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
