"""Tests for .github/scripts/patch_gitops_values.py.

This script is the highest-risk code in the repo: CI runs it to rewrite the
gitops repo's values files, commits the result to that repo's main branch,
and ArgoCD auto-syncs it to a cluster. A silent mis-edit deploys itself, so
the behaviour worth pinning down here is not just "does it set the tag" but
"does it leave everything else - especially the hand-written comments the
regex approach exists to preserve - byte-for-byte intact".

Fixtures below are trimmed copies of the real gitops values files (same
shapes: image block first, optional placeholder probe overrides, trailing
config the patch must not disturb).
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / ".github" / "scripts" / "patch_gitops_values.py"
)
_spec = importlib.util.spec_from_file_location("patch_gitops_values", _SCRIPT)
patch_gitops_values = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_gitops_values)


WITH_PROBES = """\
# PLACEHOLDER image until the app repo's CI pushes the real ECR tag here.
# This comment must survive the patch - preserving it is the whole reason
# this script uses regex line-edits instead of a YAML round-trip.
image:
  repository: public.ecr.aws/nginx/nginx
  tag: "1.27"

livenessProbe:
  httpGet:
    path: /
    port: http
readinessProbe:
  httpGet:
    path: /
    port: http

replicaCount: 1

ingress:
  enabled: true
  host: dev.devops-task.internal # trailing comment, must survive

externalSecret:
  enabled: true
  remoteRef:
    key: devops-task/dev/app-secret
"""

WITHOUT_PROBES = """\
# Staging's tag is promoted from dev, not rebuilt.
image:
  repository: 302954730632.dkr.ecr.us-east-1.amazonaws.com/devops-task-app
  tag: "sha-abc1234"

replicaCount: 2
"""

ECR_REPO = "302954730632.dkr.ecr.us-east-1.amazonaws.com/devops-task-app"


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _args(**kwargs):
    return type("Args", (), kwargs)()


# --- read-tag ----------------------------------------------------------------


def test_read_tag_strips_quotes(tmp_path, capsys):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_read_tag(_args(file=str(path)))

    assert capsys.readouterr().out.strip() == "1.27"


def test_read_tag_on_unquoted_sha_tag(tmp_path, capsys):
    path = _write(tmp_path, "values.yaml", WITH_PROBES.replace('tag: "1.27"', "tag: sha-deadbee"))

    patch_gitops_values.cmd_read_tag(_args(file=str(path)))

    assert capsys.readouterr().out.strip() == "sha-deadbee"


def test_read_tag_errors_when_no_tag_field(tmp_path):
    path = _write(tmp_path, "values.yaml", "replicaCount: 1\n")

    with pytest.raises(SystemExit):
        patch_gitops_values.cmd_read_tag(_args(file=str(path)))


# --- bump --------------------------------------------------------------------


def test_bump_sets_repository_and_tag(tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-abc1234", reset_probes=False)
    )

    result = path.read_text()
    assert f"  repository: {ECR_REPO}\n" in result
    assert '  tag: "sha-abc1234"\n' in result
    assert "public.ecr.aws/nginx/nginx" not in result


def test_bump_preserves_comments_and_unrelated_keys(tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-abc1234", reset_probes=False)
    )

    result = path.read_text()
    # the leading comment block is the entire justification for the regex approach
    assert "# PLACEHOLDER image until the app repo's CI pushes the real ECR tag here." in result
    assert "# This comment must survive the patch" in result
    assert "host: dev.devops-task.internal # trailing comment, must survive" in result
    assert "replicaCount: 1" in result
    assert "key: devops-task/dev/app-secret" in result


def test_bump_without_reset_probes_keeps_probe_blocks(tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-abc1234", reset_probes=False)
    )

    result = path.read_text()
    assert "livenessProbe:" in result
    assert "readinessProbe:" in result


def test_bump_with_reset_probes_removes_both_blocks(tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-abc1234", reset_probes=True)
    )

    result = path.read_text()
    assert "livenessProbe:" not in result
    assert "readinessProbe:" not in result
    # only the probe blocks go - the keys that followed them stay put
    assert "replicaCount: 1" in result
    assert "ingress:" in result


def test_bump_reset_probes_collapses_blank_lines(tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-abc1234", reset_probes=True)
    )

    assert "\n\n\n" not in path.read_text()


def test_bump_reset_probes_is_safe_when_probes_absent(tmp_path):
    """staging/prod get --reset-probes too, and by then dev already stripped them."""
    path = _write(tmp_path, "values.yaml", WITHOUT_PROBES)

    patch_gitops_values.cmd_bump(
        _args(file=str(path), repository=ECR_REPO, tag="sha-newer", reset_probes=True)
    )

    result = path.read_text()
    assert '  tag: "sha-newer"\n' in result
    assert "replicaCount: 2" in result


# --- promote -----------------------------------------------------------------


def test_promote_copies_image_fields_only(tmp_path):
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", WITH_PROBES)

    patch_gitops_values.cmd_promote(
        _args(source=str(source), target=str(target), reset_probes=False)
    )

    result = target.read_text()
    assert f"  repository: {ECR_REPO}\n" in result
    assert '  tag: "sha-abc1234"\n' in result
    # target's own per-env settings must NOT be pulled over from the source
    assert "replicaCount: 1" in result
    assert "replicaCount: 2" not in result
    assert "host: dev.devops-task.internal # trailing comment, must survive" in result


def test_promote_leaves_source_untouched(tmp_path):
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", WITH_PROBES)

    patch_gitops_values.cmd_promote(
        _args(source=str(source), target=str(target), reset_probes=True)
    )

    assert source.read_text() == WITHOUT_PROBES


def test_promote_with_reset_probes_strips_target_probes(tmp_path):
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", WITH_PROBES)

    patch_gitops_values.cmd_promote(
        _args(source=str(source), target=str(target), reset_probes=True)
    )

    result = target.read_text()
    assert "livenessProbe:" not in result
    assert "readinessProbe:" not in result


def test_promote_errors_when_target_has_no_image_block(tmp_path):
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", "replicaCount: 1\n")

    with pytest.raises(SystemExit):
        patch_gitops_values.cmd_promote(
            _args(source=str(source), target=str(target), reset_probes=False)
        )


# --- CLI wiring --------------------------------------------------------------
# These go through main()/argparse rather than calling cmd_* directly, so the
# exact flag spellings the three workflows invoke are pinned down here. Renaming
# a flag breaks CI against the gitops repo, which these tests would catch first.


def _run_cli(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["patch_gitops_values.py", *argv])
    patch_gitops_values.main()


def test_cli_bump_matches_dev_workflow_invocation(monkeypatch, tmp_path):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    _run_cli(
        monkeypatch,
        "bump",
        "--file", str(path),
        "--repository", ECR_REPO,
        "--tag", "sha-abc1234",
        "--reset-probes",
    )

    result = path.read_text()
    assert f"  repository: {ECR_REPO}\n" in result
    assert '  tag: "sha-abc1234"\n' in result
    assert "livenessProbe:" not in result


def test_cli_promote_matches_staging_workflow_invocation(monkeypatch, tmp_path):
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", WITH_PROBES)

    _run_cli(
        monkeypatch,
        "promote",
        "--source", str(source),
        "--target", str(target),
        "--reset-probes",
    )

    assert '  tag: "sha-abc1234"\n' in target.read_text()


def test_cli_read_tag_matches_workflow_invocation(monkeypatch, tmp_path, capsys):
    path = _write(tmp_path, "values.yaml", WITH_PROBES)

    _run_cli(monkeypatch, "read-tag", "--file", str(path))

    assert capsys.readouterr().out.strip() == "1.27"


def test_cli_requires_a_subcommand(monkeypatch):
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch)


def test_promote_round_trips_through_read_tag(tmp_path, capsys):
    """The real dev -> staging -> prod chain: what promote wrote is what read-tag reads."""
    source = _write(tmp_path, "source.yaml", WITHOUT_PROBES)
    target = _write(tmp_path, "target.yaml", WITH_PROBES)

    patch_gitops_values.cmd_promote(
        _args(source=str(source), target=str(target), reset_probes=True)
    )
    capsys.readouterr()  # discard promote's own progress line

    patch_gitops_values.cmd_read_tag(_args(file=str(target)))

    assert capsys.readouterr().out.strip() == "sha-abc1234"
