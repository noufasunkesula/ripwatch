"""Deploy templates render cleanly (sprint-1.md N-10).

deploy/user_data.sh and deploy/rw.env.tpl are Terraform templatefile() templates: `${name}` is a
Terraform input, `$${name}` a literal shell variable. shellcheck cannot read that form, so it runs
on the rendered script here instead of on the raw file.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"

_INPUT = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

TEMPLATE_VARS = {
    "region": "us-east-1",
    "artifacts_bucket": "rw-artifacts-123456789012",
    "runtime": "cool",
    "bedrock_model_id": "amazon.nova-lite-v1:0",
}


def render(text: str, values: dict[str, str]) -> str:
    """Minimal templatefile(): substitute ${name}, then turn $${ into ${. Unknown names raise."""
    assert "%{" not in text, "template directives are not supported by this renderer"

    def sub(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _INPUT.sub(sub, text).replace("$${", "${")


def rendered_user_data() -> str:
    rw_env = render((DEPLOY / "rw.env.tpl").read_text(encoding="utf-8"), TEMPLATE_VARS)
    template = (DEPLOY / "user_data.sh").read_text(encoding="utf-8")
    return render(template, {**TEMPLATE_VARS, "rw_env": rw_env})


def test_templates_only_use_known_inputs():
    for name in ("user_data.sh", "rw.env.tpl"):
        used = set(_INPUT.findall((DEPLOY / name).read_text(encoding="utf-8")))
        unknown = used - {*TEMPLATE_VARS, "rw_env"}
        assert not unknown, f"{name}: unknown inputs {unknown}"


def test_user_data_renders_without_leftover_terraform_inputs():
    script = rendered_user_data()
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in script
    assert 'REGION="us-east-1"' in script
    assert "RW_ARTIFACTS_BUCKET=rw-artifacts-123456789012" in script
    # Every ${...} left is a shell variable (upper case or a known local), never a Terraform input.
    leftovers = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", script))
    assert not leftovers & set(TEMPLATE_VARS), leftovers


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_rendered_user_data_passes_shellcheck(tmp_path):
    rendered = tmp_path / "user_data.sh"
    rendered.write_text(rendered_user_data(), encoding="utf-8", newline="\n")
    result = subprocess.run(  # noqa: S603
        ["shellcheck", "--shell=bash", str(rendered)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_cloudwatch_agent_config_ships_every_service_and_emf():
    config = json.loads((DEPLOY / "cloudwatch-agent.json").read_text(encoding="utf-8"))
    files = config["logs"]["logs_collected"]["files"]["collect_list"]
    groups = {f["log_group_name"] for f in files}
    services = ["rw-camera-sim", "rw-ingest", "rw-vision", "rw-mcp-tools", "rw-agent"]
    assert {f"/rw/worker/{s}" for s in services} | {"/rw/worker/deploy"} == groups
    assert config["logs"]["metrics_collected"] == {"emf": {}}
    assert "metrics" not in config  # host metrics are billed (sprint-1.md N-10)


def test_ssm_deploy_document_runs_rw_deploy_with_the_release():
    text = (DEPLOY / "ssm" / "rw-deploy.yaml").read_text(encoding="utf-8")
    assert "aws:runShellScript" in text
    assert "/opt/rw/bin/rw_deploy.sh {{ release }}" in text
    assert 'timeoutSeconds: "900"' in text
