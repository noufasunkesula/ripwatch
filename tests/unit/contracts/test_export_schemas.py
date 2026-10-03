from __future__ import annotations

import json

from rw.contracts import export_schemas


def test_committed_schemas_are_up_to_date():
    assert export_schemas.main(["--check"]) == 0


def test_export_writes_all_files(tmp_path):
    assert export_schemas.main(["--out", str(tmp_path)]) == 0
    for name in export_schemas.SCHEMAS:
        schema = json.loads((tmp_path / name).read_text())
        assert schema["additionalProperties"] is False


def test_check_detects_drift(tmp_path):
    export_schemas.main(["--out", str(tmp_path)])
    (tmp_path / "approval.schema.json").write_text("{}")
    assert export_schemas.main(["--out", str(tmp_path), "--check"]) == 1
