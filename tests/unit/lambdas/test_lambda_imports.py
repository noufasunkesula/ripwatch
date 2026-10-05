"""Lambdas ship stdlib + boto3 only (sprint-1.md section 3): no rw package, no third-party deps."""

import ast
import sys
from pathlib import Path

import pytest

LAMBDAS = Path(__file__).resolve().parents[3] / "lambdas"
ALLOWED_THIRD_PARTY = {"boto3", "botocore"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize(
    "path", sorted(LAMBDAS.glob("*/*.py")), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_lambda_imports_only_stdlib_and_boto3(path):
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | {"__future__", "rw_shared"}
    assert _imports(path) <= allowed, f"{path}: {_imports(path) - allowed}"
