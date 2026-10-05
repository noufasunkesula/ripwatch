import sys
import types

import pytest

from rw.common import runtime
from rw.common.runtime import EX_CONFIG, check_cv2_runtime

COOL_CV2 = "/opt/cool/venvs/python_3.12/lib/python3.12/site-packages/cv2/__init__.py"
PIP_CV2 = "/opt/rw/venv/lib/python3.12/site-packages/cv2/__init__.py"


@pytest.fixture
def fake_cv2(monkeypatch):
    def _install(path: str, version: str = "5.0.0") -> None:
        module = types.ModuleType("cv2")
        module.__file__ = path
        module.__version__ = version
        monkeypatch.setitem(sys.modules, "cv2", module)

    return _install


@pytest.fixture
def distributions(monkeypatch):
    def _set(*names: str) -> None:
        dists = [types.SimpleNamespace(metadata={"Name": n}) for n in names]
        monkeypatch.setattr(runtime.metadata, "distributions", lambda: dists)

    _set("numpy", "boto3")
    return _set


def test_cool_runtime_with_cool_cv2_passes(env, fake_cv2, distributions):
    env(RW_RUNTIME="cool")
    fake_cv2(COOL_CV2)
    info = check_cv2_runtime("c7g.large")
    assert info == {
        "variant": "cool",
        "opencv_version": "5.0.0",
        "cv2_path": COOL_CV2,
        "instance_type": "c7g.large",
    }


def test_cool_runtime_exits_78_when_cv2_is_not_under_opt_cool(env, fake_cv2, distributions):
    env(RW_RUNTIME="cool")
    fake_cv2(PIP_CV2)
    with pytest.raises(SystemExit) as exc:
        check_cv2_runtime()
    assert exc.value.code == EX_CONFIG


def test_cool_runtime_exits_78_when_pip_opencv_is_installed(env, fake_cv2, distributions):
    env(RW_RUNTIME="cool")
    fake_cv2(COOL_CV2)
    distributions("numpy", "opencv-python-headless")
    with pytest.raises(SystemExit) as exc:
        check_cv2_runtime()
    assert exc.value.code == EX_CONFIG


def test_cool_runtime_exits_78_when_cv2_is_missing(env, monkeypatch, distributions):
    env(RW_RUNTIME="cool")
    monkeypatch.setitem(sys.modules, "cv2", None)
    with pytest.raises(SystemExit) as exc:
        check_cv2_runtime()
    assert exc.value.code == EX_CONFIG


def test_std_runtime_allows_pip_opencv(env, fake_cv2, distributions):
    env(RW_RUNTIME="std-arm")
    fake_cv2(PIP_CV2)
    distributions("opencv-python-headless")
    assert check_cv2_runtime()["variant"] == "std-arm"


def test_local_runtime_uses_the_real_cv2(env):
    env()
    info = check_cv2_runtime()
    assert info["variant"] is None
    assert info["opencv_version"].startswith("5.")
    assert info["instance_type"] == "local"
