# Deployment & release topology

This file describes the workflows that build, validate, and publish
simdref. It is a living document — when you change a workflow, update
this file.

## Workflow dependency chain

```
 push to main
     │
     ▼
┌──────────┐    ┌──────────────────────┐    ┌──────────────────┐
│    CI    │ ─► │ Build Data & Deploy  │ ─► │   Deploy Pages   │
│ (ci.yml) │    │  (build-data.yml)    │    │   (pages.yml)    │
└──────────┘    │  — refreshes         │    │  — pulls         │
                │    data-latest       │    │    data-latest   │
                └──────────┬───────────┘    └──────────────────┘
                           │
                           ▼
                ┌──────────────────────────────┐
                │  Validate Release Assets     │
                │  (validate-release-assets)   │
                │  — pulls data-latest, runs   │
                │    full test suite against   │
                │    the freshly-published     │
                │    catalog                   │
                └──────────────────────────────┘
```

- **CI** fires on every push/PR. On main, a green CI fires Build Data &
  Deploy via `workflow_run`. A red or cancelled CI does *not* trigger
  the chain — `data-latest` only refreshes from a validated commit.
- **Build Data & Deploy** rebuilds the full catalog (x86 SDM + uops.info
  + Arm ACLE + RISC-V unified-db + RVV intrinsic doc) and publishes
  `data-latest`. On `release: published`, it also publishes
  `data-v<version>`.
- **Deploy Pages** consumes the freshly-published `data-latest`, rebuilds
  the static web bundle, and deploys to GitHub Pages.
- **Validate Release Assets** independently pulls `data-latest` and
  runs the full test suite against it — a red here means the release
  artefact itself is broken, even if every upstream build was green.

## Release flow

```
 human: gh workflow run release-candidate.yml -f version=X.Y.Z
                 │                                ▲
                 │ dry_run=true (default)         │ review
                 ▼                                │
        ┌─────────────────┐                       │
        │   preflight     │   — version match, tag absent, PyPI absent
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │    ci (reuse)   │   — full ci.yml as reusable workflow
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │  build-wheel    │   — uv build + twine check
        └────────┬────────┘
                 ▼
        ┌─────────────────┐
        │  install-smoke  │   — pip install wheel in fresh venv
        └────────┬────────┘
                 ▼       (only if dry_run=false)
        ┌─────────────────┐
        │       tag       │   — creates & pushes vX.Y.Z
        └────────┬────────┘
                 ▼
       ┌───────────────────────┐
       │  release.yml (tagged) │   — twine check, install round-trip,
       │                       │     PyPI publish (via pypi env),
       │                       │     GitHub Release create
       └───────────┬───────────┘
                   ▼
       release: published → Build Data & Deploy → publishes
                                                 `data-v<version>`
                                                 + refreshes
                                                 `data-latest`
```

## Cutting a release

1. Bump `pyproject.toml` → `[project].version = "X.Y.Z"` and commit to
   main. Wait for CI/Build Data/Pages to go green.
2. `gh workflow run release-candidate.yml -f version=X.Y.Z -f dry_run=true`
   — verifies every gate without side effects.
3. Inspect the run. If green, re-run with `dry_run=false` to push the
   tag.
4. The `release.yml` workflow fires on the new tag, publishes to PyPI
   (requires approval on the `pypi` environment), and creates the
   GitHub Release.
5. The release publish event triggers Build Data & Deploy one more
   time, this time producing `data-v<version>` alongside the refreshed
   `data-latest`.

## Recovery playbook

- **`data-latest` download fails in Pages/Validate** — the upstream
  Build Data & Deploy either skipped (because CI was red) or its `build`
  job genuinely failed. Re-run CI and the chain will self-heal.
- **`release-candidate / preflight` fails** — one of (a) pyproject
  version mismatches input, (b) tag already exists, (c) version already
  on PyPI. Fix the underlying state, don't force-push the tag.
- **`release.yml` stuck on PyPI publish** — the `pypi` GitHub
  environment requires manual approval. Approve in the run's page.

## Configured environments

- `pypi` — gated approval for PyPI trusted-publisher OIDC token.
- `github-pages` — auto-approved deploy target for the web bundle.
- `testpypi` — used by `nightly-testpypi.yml` for pre-release smoke.
