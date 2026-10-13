# CI and delivery

## Continuous integration

`.github/workflows/format-check.yml` extends the existing **Format Check** workflow
and preserves its **Check code formatting** check name. It runs for PRs targeting
`main` or `develop`, pushes to `main`, and manual dispatch. There are no path
filters, so a required check also runs for documentation-only PRs.

The Ubuntu 24.04 job installs Python from `.python-version` (3.12), the current
stable uv, Node 24, and pnpm 11. `uv sync --locked` validates project metadata and installs
runtime and development dependencies from `uv.lock`. It runs Ruff formatting/import
lint, the Python unittest suite (including its Node regression scripts), and
`web/app.js` syntax checks. uv's cache is keyed on the lockfile, project metadata,
Python selection, and workflow. The pnpm store is cached by `pnpm-lock.yaml`.

Versions are selected at the level needed for compatibility: Python 3.12 and
Node 24 accept patch updates; pnpm and Actions use major tags; uv has no exact version pin.
Ruff and TestClient's httpx are declared as development dependencies with lower
bounds in `pyproject.toml`. Their resolved versions live in the same lockfile as
runtime dependencies, so local and CI checks agree without duplicating exact
pins in the workflow. Refresh deliberately with `uv lock --upgrade-package ruff`
(or the dependency being updated), and commit the lockfile. Runtime package
versions are preserved by this CI change.

`requires-python = ">=3.10"` retains the existing compatibility declaration;
`.python-version` selects the documented/tested 3.12 environment. Ruff infers its
target from `requires-python`, removing the conflicting explicit `py312` target.
This single CI job does not certify every declared Python version.
`tool.uv.package = false` is appropriate for the current source-run application;
there is no distribution build backend. Model dependencies remain required, and
the project comment now reflects the startup failure when model loading fails.

The token has only `contents: read`, checkout does not persist credentials, and
the workflow does not consume secrets. New runs cancel older runs for the same
PR or branch; each job has a 30-minute limit. There is no deployment job.

## Free runner selection

`ubuntu-24.04` (Linux x64) provides **4 vCPU, 16 GB RAM, and 14 GB SSD** for public
repositories. It ties the largest free standard CPU/RAM tier with Windows and
Linux ARM64, while macOS Intel offers 4 CPU/14 GB and macOS ARM64 3 CPU/7 GB.
Linux x64 keeps the existing shell and scientific Python wheel compatibility;
the previous PR run already verified it. Changing to Windows or ARM would not
increase CPU/RAM. The OS label stays explicit to avoid an unrelated image upgrade.

GitHub documents standard runner compute as free and unlimited for public
repositories; larger runners are paid even for public repositories. This workflow
uses no larger runner, paid runner group, or additional billing configuration.
Cache storage keeps the existing repository allowance; it is not increased.
Specifications verified on 2026-10-13:
[runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories)
and [billing conditions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Local validation

Run equivalent checks locally from the repository root:

```sh
uv python install
uv sync --locked
uv run --no-sync ruff format --check .
uv run --no-sync ruff check .
uv run --no-sync python -m unittest discover -s tests -v
node --check web/app.js
```

Node must be installed separately. Ruff currently checks formatting and import
order, following `pyproject.toml`. There is no configured static type checker,
frontend bundler, or distributable package (`tool.uv.package = false`), so this
first pass does not invent type-check or build commands.

Unit tests mock AI backends and skip application lifespan/model warm-up. The
optional real spaCy segmenter case skips when `en_core_web_sm` is not installed.
CI therefore does not certify model downloads/inference, subscription CLI login,
or an interactive browser session. Those require separate integration checks.
Linux installs the existing locked torch/torchvision dependencies unchanged;
the cold install can be large. Do not switch just one of them to a CPU index.

## JavaScript formatting adoption

Oxfmt is the JS formatter; Ruff remains the Python formatter. Development scripts
use the familiar `format` / `format:check` names and pnpm. `oxfmt: ^0` allows
updates within its current major; `pnpm-lock.yaml` records the tested resolution.
The shared style is two spaces, semicolons, LF, trailing commas, arrow parentheses,
and 100-column wrapping. Double quotes match Sundark's existing JavaScript.
No frontend framework, build step, or JavaScript runtime dependency is added.

```sh
pnpm install --frozen-lockfile
pnpm run format:check
# Writes source formatting; run only when ready for the separate migration:
pnpm run format
```

The check covers JavaScript in `web/` and `tests/`, including future test files.
**It is advisory during adoption**: all four existing JS files currently need
formatting. CI emits a warning and job-summary entry when it fails, while Python
format/lint, tests, and JavaScript syntax still block the job. Installation errors
also block the job. A green workflow does not mean the Oxfmt check passed.
Source files are deliberately not reformatted here because PR #4 is changing
`web/app.js`. After that work lands, format the sources in a dedicated change and
remove `continue-on-error` and the advisory-report step to make this gate required.

## Delivery decision

Sundark is documented as a personal local app served on `127.0.0.1:8743`, using
the user's signed-in Claude Code or Hermes subscription. No hosted deployment
target or release packaging policy is defined in the repository.

Read-only GitHub inspection on 2026-10-13 found: default branch `main`, the
existing Format Check workflow plus GitHub's dependency-graph workflow, no
deployment records or environments, Pages disabled, no rulesets, and no classic
protection on `main`. Actions is enabled with a read-only default token. This is
a snapshot, not a guarantee that deployment is absent outside GitHub.

Before implementing CD, choose:

1. Delivery target: a versioned local installation/release, or a named hosted
   service with an agreed authentication and data-persistence design.
2. Trigger and approval: release tag/manual approval versus automatic delivery
   after successful `main` CI, plus rollback/version retention policy.
3. For hosting, the environment owner and approved credential mechanism. Do not
   place a user's subscription session or uploaded papers in GitHub artifacts.

After a successful hosted CI run, the owner can separately approve making
**Check code formatting** required on `main` and requiring PR review. Repository
rules, environments, secrets, and actual deployment are not changed by this PR.
