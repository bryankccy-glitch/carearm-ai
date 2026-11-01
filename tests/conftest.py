import os
import sys
from pathlib import Path

# Linux 無螢幕環境使用 EGL；macOS 使用 MuJoCo 預設（CGL）
if sys.platform.startswith("linux"):
    os.environ.setdefault("MUJOCO_GL", "egl")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 測試一律用暫存資料庫：服藥紀錄會跨重啟保留，不能讓測試寫進真正的紀錄
import tempfile  # noqa: E402

os.environ["CAREARM_DB"] = str(Path(tempfile.mkdtemp(prefix="carearm_test_")) / "test.sqlite")

import pytest  # noqa: E402

from carearm.settings import load_config  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def env(cfg):
    from carearm.simulation.scene import make_carearm_env

    e = make_carearm_env(cfg)
    yield e
    e.close()
