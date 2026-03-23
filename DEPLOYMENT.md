# Deployment

## Platform Order

Use this order for a clean rollout that validates the private services before
you publish the public SDK packages:

1. Tag or pin `maivn-shared` in GitHub so service repos can consume an immutable ref.
2. Tag or pin `maivn-internal-shared` in GitHub and record the immutable ref that services will consume.
3. Create the production Supabase project and apply the platform migration pipeline.
4. Deploy `maivn-agents`.
5. Deploy `maivn-server`.
6. After service validation, publish `maivn-shared` to PyPI.
7. Publish `maivn` to PyPI.
8. Publish `maivn-studio` to PyPI.

## Repo Role

`maivn` is the public SDK repo and public PyPI package. It must only be released after the target
`maivn-shared` version already exists on PyPI. The SDK release path should remain fully public and
must not depend on `maivn-internal-shared`.

`maivn` also owns the public `maivn` console script. The public `maivn-studio` companion package
installs the SDK and enables `maivn studio` without `uv run`.

## GitHub Setup

1. Create the repo as public.
2. Set `main` as the protected default branch.
3. Enable GitHub Actions.
4. Create an environment named `pypi`.
5. Configure PyPI Trusted Publishing for this repository and workflow:
   `.github/workflows/publish-pypi.yml`.
6. Set these optional repository variables if `maivn-shared` lives outside the default owner or
   if you want CI pinned to a non-default ref:
   - `MAIVN_SHARED_REPO`
   - `MAIVN_SHARED_REF`

## Release Steps

1. Confirm the target `maivn-shared` version is already published on PyPI.
2. Update the SDK version and, if needed, the `maivn-shared` dependency range in `pyproject.toml`.
3. For local verification before `maivn-shared` is published, inject a temporary local source
   override or otherwise point the environment at the exact shared-repo ref you intend to support.
4. Run local verification:
   ```bash
   uv sync --frozen
   uv run --no-sync ruff check .
   uv run --no-sync pyright
   uv run --no-sync pytest
   ```
5. Merge the release commit to `main`.
6. Create and push an annotated tag such as `v0.1.0`.
7. Confirm the `Publish PyPI` workflow succeeds.
8. Verify installation from a clean environment:
   ```bash
   pip install maivn==0.1.0
   ```

## Rollback

1. Yank the affected PyPI version if needed.
2. Cut a new patch release with the fixed `maivn-shared` range or SDK changes.
3. Do not change the contents behind an existing tag.
