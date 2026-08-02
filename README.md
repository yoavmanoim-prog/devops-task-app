# devops-task-app

A small FastAPI sample app whose only real job is to prove the pipeline end to end: one plain
env var, one file from a mounted Secret, and a CI/CD setup that builds once and promotes the
exact same image through dev → staging → a manual production gate.

Part of a 3-repo submission:

- [devops-task-infra](https://github.com/yoavmanoim-prog/devops-task-infra) - Terraform/Terragrunt IaC (provisions the ECR repo + GitHub OIDC role this repo's CI uses)
- [devops-task-gitops](https://github.com/yoavmanoim-prog/devops-task-gitops) - Helm chart + ArgoCD manifests (this repo's CI writes image tags here, never anything else)
- **app** (this repo)

## Endpoints

| Path | Behavior |
|---|---|
| `GET /` | `{"message", "app_env" (from the `APP_ENV` env var), "secret_message"}` - `secret_message` is the contents of a file mounted from the cluster's Secret (`SECRET_FILE_PATH`, defaults to `/etc/secrets/message`), or `null` if not present. Echoed deliberately - it's a demo secret, so this is what makes the ExternalSecrets → K8s Secret → mounted-file pipeline visibly provable. |
| `GET /healthz` | Liveness - always `200`. |
| `GET /readyz` | Readiness - also always `200` rather than gated on the secret file existing (a hard-gated check would leave the pod permanently NotReady until the real AWS secret exists). |
| `GET /docs` | Free from FastAPI - interactive OpenAPI docs. |

## Local dev

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt -r config/requirements-dev.txt

ruff check --config config/pyproject.toml .
pytest -c config/pyproject.toml --rootdir=. --cov=src --cov-report=term-missing --cov-fail-under=80

uvicorn src.main:app --reload
```

Config files (`pyproject.toml`, `requirements*.txt`) live under `config/` to keep the repo root
readable - `--config`/`-c`/`--rootdir` point the tools at them explicitly since they don't live at
the conventional top-level location.

## Docker

```sh
docker build -t devops-task-app .
docker run -p 8000:8000 devops-task-app
```

Multi-stage build, `python:3.13-slim` pinned by content digest (not a tag), runs as non-root
`uid 1000` - matches the `gitops` chart's `securityContext.runAsUser`.

## CI/CD

Three per-environment workflow files under `.github/workflows/` (plus `lint.yaml` and
`rollback.yaml`) - each does exactly one thing, no shared file, no branch-conditional jobs:

- **`dev.yaml`** (push to `dev`) - lint + test → build, smoke-test the actual built container
  (not a separate rebuild), push to ECR tagged `sha-<shortsha>` (+ semver on `v*` tags) → bump
  `gitops/apps/dev/values-dev.yaml`.
- **`staging.yaml`** (push to `staging`) - verify the source tag actually exists in ECR → promote
  it (no rebuild) into `gitops/apps/dev/values-staging.yaml` → `helm template` the result to fail
  fast if it's broken → commit + push.
- **`prod.yaml`** (push to `main`) - same shape, `apps/dev/values-staging.yaml` →
  `apps/prod/values-production.yaml`. Production's `Application` has no automated sync policy, so
  this does not itself deploy anything - a human still clicks Sync in ArgoCD.
- **`rollback.yaml`** (manual `workflow_dispatch`) - pins production to an **existing** ECR tag.
  Nothing is rebuilt; it reuses the same `verify-ecr-tag` guard, the same patch script (`bump`,
  with an explicit `--tag`), the same `helm template` check and the same commit-and-push. Needed
  because the promotion path above can only ever move production to staging's *current* tag, and
  nothing in these workflows reads app source - so reverting app code doesn't roll back a deploy
  either. The alternative was hand-editing the gitops repo, which breaks the rule that CI is its
  sole writer. Like a promotion, it only lands the tag in git: a human still clicks Sync.
- **`lint.yaml`** (every PR + push to any of the 3 long-lived branches) - `actionlint` over the
  whole repo, so a future broken workflow or composite action gets caught in CI, not just by
  whoever happens to run it locally.

**Branching model**: `main` **is** the production branch - there's no separate `prod` branch.
`dev`/`staging`/`main` are long-lived; feature branches → PR → `dev` is where real builds happen.
Merging `dev` → `staging` or `staging` → `main` (PRs within this repo) never rebuilds - it re-tags
the *same* image digest forward, so what reaches production is provably the exact thing already
tested in dev.

Every third-party GitHub Action used (`actions/checkout`, `aws-actions/configure-aws-credentials`,
`aws-actions/amazon-ecr-login`, `docker/setup-buildx-action`, `docker/build-push-action`,
`azure/setup-helm`) is a Marketplace "Verified Creator" publisher, pinned to a full commit SHA.
Everything else (ECR login mechanics beyond the login action itself, git commit/push, YAML
patching) is either a local composite action under `.github/actions/` or a plain script
(`.github/scripts/patch_gitops_values.py`) - never an unnecessary marketplace dependency.

## Required repo configuration

Two things this repo's CI needs that aren't (and shouldn't be) committed:

- **`GITOPS_REPO_TOKEN`** (repository secret) - a fine-grained GitHub PAT scoped to just
  `devops-task-gitops`, Contents: read/write. Lets CI check out and push to that repo.
- **`AWS_OIDC_ROLE_ARN`** (repository **variable**, not secret - ARNs aren't sensitive) - the
  `github-oidc` Terraform module's `role_arn` output, from `devops-task-infra`. Doesn't exist
  until that module has actually been applied - see that repo's README.

## Known limitations

- `/readyz` is intentionally trivial rather than gated on the mounted secret's presence (see
  Endpoints table above) - a deliberate tradeoff to avoid pods stuck permanently NotReady before
  the real secret exists.
- `secret_message` on `/` echoes the mounted secret's actual value - fine for this demo secret
  (we control its content), not a pattern to copy for a real credential.
- **A production rollback is not sticky.** `prod.yaml` triggers on *any* push to `main` with no
  `paths:` filter, and unconditionally copies staging's current tag onto production. So after a
  rollback pins production to `sha-GOOD`, the next merge to `main` - even a README fix by someone
  who doesn't know a rollback happened - re-reads `values-staging.yaml` (still `sha-BAD`, since
  staging wasn't rolled back) and writes it straight back. The manual ArgoCD Sync stops it going
  live silently, but the rollback has been erased from git and the next person to hit Sync ships
  the bad image.

  Worth being precise about why the obvious guard doesn't help: a "skip if the tag already matches
  what's deployed" check fires *exactly* when you need it to hold still, because the rollback is
  what made the tags differ. That's an efficiency control, not a safety one - it has no concept of
  intent. The real fixes are a GitHub Environment on `prod.yaml` with a required reviewer (the job
  waits for a human who can decline an unrelated promotion - no new state, and free on public
  repos), or a `paths:` filter so unrelated merges don't trigger it at all. Neither is applied.
- **There is no staging rollback**, only production. Staging's `Application` is auto-synced with
  `selfHeal`, so a staging rollback would take effect immediately but hold only until the next
  `dev` → `staging` merge re-promoted dev's tag - which is arguably correct, since staging exists
  to track dev. Adding it is one `environment` choice input on `rollback.yaml`.
- **Staging's values file does double duty**, which couples the two environments: it is both "what
  staging runs" and the source `prod.yaml` reads to decide what production should run. So rolling
  staging back would also change what the next merge to `main` proposes for production.
