# CI and delivery

## Continuous integration

`.github/workflows/format-check.yml` extends the existing **Format Check** workflow
and preserves its **Check code formatting** check name. It runs for PRs targeting
`main` or `develop`, pushes to `main`, and manual dispatch. There are no path
filters, so a required check also runs for documentation-only PRs.

The Ubuntu 24.04 job installs Python from `.python-version` (3.12), uv 0.10.0,
and Node 24. It runs Ruff 0.14.10 formatting/import lint, validates and installs
`uv.lock` with `uv sync --locked`, runs the Python unittest suite (including its
Node regression scripts), and checks `web/app.js` syntax. uv's dependency cache
is keyed on the lockfile, project metadata, Python pin, and workflow. Actions are
pinned to full commit SHAs; update the SHA and version comment together.

The token has only `contents: read`, checkout does not persist credentials, and
the workflow does not consume secrets. New runs cancel older runs for the same
PR or branch; each job has a 30-minute limit. There is no deployment job.

Run equivalent checks locally from the repository root:

```sh
uv python install
uvx ruff@0.14.10 format --check .
uvx ruff@0.14.10 check .
uv sync --locked
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
