# Deployment & release topology

This file documents the simdref pipeline. When you change a workflow,
update this file.

## Pipeline (`.github/workflows/ci.yml`)

Single workflow, single DAG, explicit `needs:` for ordering. No
opportunistic skipping — every edge is a hard dependency.

```
                    ┌──────────── test ────────────┐
                    │                              │
 build-catalog ─────┤                              ├─── publish-data ──┬── deploy-pages
 (creates catalog   │                              │   (on main /       │   (on main /
  artifact)         └── package (parallel) ────────┘    release /       │    release)
                                                       schedule)        │
                                                                        └── validate-release
                                                                            (on main / release)
```

### Phase 1 — data creation
- **`build-catalog`** — installs LLVM 22, vendors RISC-V sources, runs
  `simdref build` (SDM is always included), validates upstream
  ingestion, asserts measured+modeled perf rows, uploads the catalog
  bundle as a workflow artefact (`catalog`).

### Phase 2 — data usage (parallel)
- **`test`** *(needs: build-catalog)* — downloads the artefact,
  installs the package, asserts schema is current, runs the full
  pytest suite, syntax-checks Python + JS sources, runs `simdref
  doctor`, asserts catalog structural invariants.
- **`package`** *(parallel)* — `uv build` → `twine check`, uploads the
  wheel/sdist artefact.

### Phase 3 — deploy (only on main, release, schedule, or dispatch)
- **`publish-data`** *(needs: test, package)* — downloads the catalog
  artefact, publishes `data-latest`. On `release: published` events it
  also publishes `data-v<version>`.
- **`deploy-pages`** *(needs: publish-data)* — downloads the catalog
  artefact (already carries `web/`), uploads + deploys to GitHub Pages.
- **`validate-release`** *(needs: publish-data)* — on a fresh runner,
  `simdref update --from-release` pulls `data-latest` and runs the full
  test suite against it. Independent paranoid check that the published
  artefact is consumable end-to-end.

### Trigger matrix
- **pull_request** → `build-catalog` → `test`, `package`. Phase 3 skips.
- **push to main** → full chain.
- **push to other branches** → phases 1–2 only.
- **release: published** → full chain + `data-v<version>` publication.
- **schedule (weekly Mon 00:00 UTC)** → full chain. Refreshes
  `data-latest` from upstream drift.
- **workflow_dispatch** → full chain.
- **workflow_call** (used by release-candidate.yml) → phases 1–2 only.

Caching: none. Every run rebuilds the catalog from upstream. Trades
runtime for guaranteed freshness; no chance of a stale cache masking a
schema/ingestion regression.

## Release flow

```
 human: gh workflow run release-candidate.yml -f version=X.Y.Z -f dry_run=true
                 │
                 ▼
   ┌──────────────────────┐
   │   preflight          │  — version match, tag absent, PyPI absent
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   ci (reusable)      │  — full ci.yml pipeline (phases 1–2)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   build-wheel        │  — uv build + twine check
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   install-smoke      │  — pip install wheel, simdref --version
   └──────────┬───────────┘
              ▼       (only when dry_run=false)
   ┌──────────────────────┐
   │   tag                │  — git tag -a vX.Y.Z && push
   └──────────┬───────────┘
              ▼
     tag push triggers release.yml → PyPI publish (gated by `pypi`
     environment approval) + GitHub Release creation → triggers ci.yml
     with `release: published` → publishes `data-v<version>` +
     refreshes `data-latest`.
```

## Cutting a release

1. Bump `pyproject.toml` → `[project].version = "X.Y.Z"`, commit to main.
2. Wait for CI on main to go green.
3. `gh workflow run release-candidate.yml -f version=X.Y.Z -f dry_run=true`
   — proves every gate without side effects.
4. Inspect the dry-run. If green, re-dispatch with `dry_run=false` to
   push the tag.
5. `release.yml` fires on the tag → approve the `pypi` environment in
   the GitHub UI → PyPI publish + GitHub Release.
6. The release-published event re-triggers `ci.yml` which publishes
   `data-v<version>` alongside the refreshed `data-latest`.

## Configured environments

- `pypi` — manual-approval gate for PyPI trusted-publisher OIDC.
- `github-pages` — auto-approved deploy target.
- `testpypi` — used by `nightly-testpypi.yml` for pre-release smoke.

## Recovery playbook

- **Catalog build fails** — upstream source drift. Check the
  `validate-upstream-ingestion` step; pin or patch the ingester.
- **`publish-data` fails** — GitHub Releases API flake. Re-run the job.
- **`deploy-pages` fails after `publish-data` green** — Pages env
  permission issue. Check the `github-pages` environment settings.
- **`validate-release` fails** — the published `data-latest` is
  broken. Investigate the catalog bundle in the previous
  `build-catalog` run's artefact. Do not tag a release until green.
- **`release-candidate / preflight` fails** — one of (a) pyproject
  mismatch, (b) tag already exists, (c) version already on PyPI. Fix
  upstream state; do not force a tag.
- **`release.yml` stuck on PyPI publish** — `pypi` environment awaits
  manual approval.
