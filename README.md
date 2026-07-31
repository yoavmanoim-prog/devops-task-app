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

Three fully independent workflow files under `.github/workflows/` - each does exactly one thing
for exactly one branch, no shared file, no branch-conditional jobs:

- **`dev.yaml`** (push to `dev`) - lint + test → build, smoke-test the actual built container
  (not a separate rebuild), push to ECR tagged `sha-<shortsha>` (+ semver on `v*` tags) → bump
  `gitops/apps/dev/values-dev.yaml`.
- **`staging.yaml`** (push to `staging`) - verify the source tag actually exists in ECR → promote
  it (no rebuild) into `gitops/apps/dev/values-staging.yaml` → `helm template` the result to fail
  fast if it's broken → commit + push.
- **`prod.yaml`** (push to `main`) - same shape, `apps/dev/values-staging.yaml` →
  `apps/prod/values-production.yaml`. Production's `Application` has no automated sync policy, so
  this does not itself deploy anything - a human still clicks Sync in ArgoCD.
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
