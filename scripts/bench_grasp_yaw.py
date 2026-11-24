"""手腕旋轉夾取比較：藥盒靠得很近時，夾爪轉向（align_yaw）是否提高成功率。

目標盒（noon）放在 (0.50, 0.0)，左右（夾爪開合方向）各放一個鄰盒，改變中心距與目標盒角度，
比較 align_yaw=true／false 的 MuJoCo 物理結果。成功判定沿用 executor（含鄰盒位移 ≤ 1.5 cm）。
只停用 OpenGL renderer；接觸、控制器、時間積分都是真正的 MuJoCo。
用法：.venv/bin/mjpython scripts/bench_grasp_yaw.py [--out results.json]
"""
from __future__ import annotations

import argparse, json, sys, time
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import mujoco

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from carearm.settings import load_config  # noqa: E402
from carearm.simulation.scene import SceneConditions, make_carearm_env  # noqa: E402
from carearm.robot.executor import RetrievalExecutor  # noqa: E402


class NoGraphics:
    def __init__(self, *a, **k): pass
    def render(self): return None
    def close(self): pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaws", default="0,15,30,45")
    ap.add_argument("--gaps", default="0.060,0.070,0.080,0.100")
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = load_config()
    with patch.object(mujoco, "Renderer", NoGraphics):
        env = make_carearm_env(cfg)
    rows = []
    for gap in [float(g) for g in a.gaps.split(",")]:
        for yaw in [float(y) for y in a.yaws.split(",")]:
            for align in (True, False):
                c = deepcopy(cfg); c["grasp"]["align_yaw"] = align
                pos = {"noon": (0.50, 0.0, yaw), "morning": (0.50, -gap, 0.0), "evening": (0.50, gap, 0.0)}
                env.reset(seed=0, options={"conditions": SceneConditions(positions=pos)})
                ex = RetrievalExecutor(env, c)
                ex.move_to_observation_pose()
                t = time.time()
                try:
                    r = ex.execute("noon", (0.50, 0.0), yaw_deg=yaw).to_dict()
                except Exception as e:  # 工作範圍外等例外如實記錄
                    r = {"success": False, "error": f"{type(e).__name__}: {e}"}
                row = {"gap_mm": round(gap * 1000), "yaw": yaw, "align": align, "success": bool(r.get("success")),
                       "grasp": r.get("grasp_success"), "error": r.get("error"), "sec": round(time.time() - t, 1)}
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    env.close()
    if a.out:
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print("\n中心距(mm) 角度  對齊／固定")
    for gap in sorted({r["gap_mm"] for r in rows}):
        for yaw in sorted({r["yaw"] for r in rows}):
            s = {r["align"]: r["success"] for r in rows if r["gap_mm"] == gap and r["yaw"] == yaw}
            print(f"{gap:>8} {yaw:>5.0f}°  {'成功' if s.get(True) else '失敗'}／{'成功' if s.get(False) else '失敗'}")


if __name__ == "__main__":
    main()
