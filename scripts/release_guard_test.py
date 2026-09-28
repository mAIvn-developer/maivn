"""Offline proof for release_guard: only the exact release tag may publish."""

# These are direct unittest cases, so each method's name states the assertion and
# unittest assertions retain their normal failure messages.
# ruff: noqa: D101, D102, PT009

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_guard


class ReleaseRefChecks(unittest.TestCase):
    def test_branch_named_like_the_release_tag_is_refused(self) -> None:
        env = {
            'GITHUB_REF_TYPE': 'branch',
            'GITHUB_REF': 'refs/heads/v2.0.0',
            'GITHUB_REF_NAME': 'v2.0.0',
        }
        self.assertIn('only from a tag', release_guard.check_release_ref(env, '2.0.0'))

    def test_workflow_dispatch_from_master_is_refused(self) -> None:
        env = {
            'GITHUB_REF_TYPE': 'branch',
            'GITHUB_REF': 'refs/heads/master',
            'GITHUB_REF_NAME': 'master',
        }
        self.assertIsNotNone(release_guard.check_release_ref(env, '2.0.0'))

    def test_missing_ref_variables_are_refused(self) -> None:
        self.assertIsNotNone(release_guard.check_release_ref({}, '2.0.0'))

    def test_other_tag_is_refused(self) -> None:
        env = {
            'GITHUB_REF_TYPE': 'tag',
            'GITHUB_REF': 'refs/tags/v2.0.1',
            'GITHUB_REF_NAME': 'v2.0.1',
        }
        self.assertIn("expected 'refs/tags/v2.0.0'", release_guard.check_release_ref(env, '2.0.0'))

    def test_tag_with_inconsistent_ref_name_is_refused(self) -> None:
        env = {
            'GITHUB_REF_TYPE': 'tag',
            'GITHUB_REF': 'refs/tags/v2.0.0',
            'GITHUB_REF_NAME': 'v2.0.1',
        }
        self.assertIsNotNone(release_guard.check_release_ref(env, '2.0.0'))

    def test_exact_release_tag_passes_the_ref_checks(self) -> None:
        env = {
            'GITHUB_REF_TYPE': 'tag',
            'GITHUB_REF': 'refs/tags/v2.0.0',
            'GITHUB_REF_NAME': 'v2.0.0',
        }
        self.assertIsNone(release_guard.check_release_ref(env, '2.0.0'))


if __name__ == '__main__':
    unittest.main()
