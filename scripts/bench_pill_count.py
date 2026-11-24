"""藥丸計數基準測驗：在真實 MuJoCo 手腕 RGB-D 影像上量 PillCounter 的準確率。

隨機擺放 0–5 顆藥丸（球與膠囊、半徑依設定隨機），讓物理沉降後用 sim.pills_in_box 當真值。
用法：.venv/bin/mjpython scripts/bench_pill_count.py --n 60 --seed 7 [--counter module:Class]
"""
from __future__ import annotations

import argparse, importlib, json, sys
from collections import Counter
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from carearm.settings import load_config  # noqa: E402
from carearm.simulation.scene import SceneConditions, make_carearm_env, well_spans  # noqa: E402
from carearm.robot.executor import RetrievalExecutor  # noqa: E402

POS = {"morning": (0.46, -0.15, 8), "noon": (0.55, 0.02, -6), "evening": (0.47, 0.155, 3)}


def layout(rng, cfg, n):
    spans = well_spans(cfg)
    lim = cfg["box"]["half_size"][1] - cfg["box"]["wall"]
    r0, r1 = cfg["pills"]["radius_range"]
    out = []
    for i in range(n):
        a, b = spans[rng.integers(len(spans))]
        r = float(rng.uniform(r0, r1))
        out.append({"x": float(rng.uniform(a + r, b - r)), "y": float(rng.uniform(-lim + r * 1.3, lim - r * 1.3)), "r": r, "color": int(rng.integers(4))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--counter", default="carearm.perception.pills:PillCounter")
    ap.add_argument("--out")
    ap.add_argument("--dump")
    ap.add_argument("--cam-height", type=float)
    ap.add_argument("--debug", help="scene:box 印出偵測細節")
    a = ap.parse_args()
    mod, cls = a.counter.split(":")
    Counter_ = getattr(importlib.import_module(mod), cls)
    cfg = load_config()
    if a.cam_height:
        cfg["adherence"]["inspect_camera_height"] = a.cam_height
    env = make_carearm_env(cfg)
    sim = env.unwrapped
    counter = Counter_(cfg)
    rng = np.random.default_rng(a.seed)
    rows = []
    for k in range(a.n):
        pills = {b: layout(rng, cfg, int(rng.integers(0, cfg["pills"]["max_per_box"] + 1))) for b in POS}
        env.reset(seed=int(k), options={"conditions": SceneConditions(positions=POS, pills=pills)})
        for _ in range(400):                       # 讓重疊的藥丸被物理推開、沉降
            mujoco.mj_step(sim.model, sim.data)
        for box in POS:
            truth = sim.pills_in_box(box)
            xy = sim.box_position(box)[:2]
            RetrievalExecutor(env, cfg).move_wrist_camera_over((float(xy[0]), float(xy[1])))
            res = counter.inspect(sim.render_wrist(), sim.wrist_camera_geometry(), sim.render_wrist_depth(seed=k))
            if a.debug == f"{k}:{box}":
                print("DEBUG", truth, res.count, res.reasons, res.blobs, [p.round(4).tolist() for p in sim.pill_positions(box)][:truth+1])
            if a.dump and res.count != truth:
                import cv2
                Path(a.dump).mkdir(parents=True, exist_ok=True)
                d = sim.render_wrist_depth(seed=k); rgb = sim.render_wrist()
                v = d.copy(); m = v > 0; lo, hi = np.percentile(v[m], 2), np.percentile(v[m], 60)
                g = np.clip((hi - v) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
                cv2.imwrite(f"{a.dump}/s{k}_{box}_t{truth}_c{res.count}.png", np.hstack([cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), cv2.applyColorMap(g, cv2.COLORMAP_JET)]))
            rows.append({"scene": k, "box": box, "truth": int(truth), "count": int(res.count), "uncertain": bool(res.uncertain)})
    env.close()
    n = len(rows)
    exact = sum(r["count"] == r["truth"] for r in rows)
    sure = [r for r in rows if not r["uncertain"]]
    sure_wrong = sum(r["count"] != r["truth"] for r in sure)
    empty_err = sum((r["count"] == 0) != (r["truth"] == 0) for r in rows)
    print(f"counter={a.counter} seed={a.seed} cases={n}")
    print(f"exact {exact}/{n} = {exact/n:.1%} | uncertain {n-len(sure)} | confident-but-wrong {sure_wrong} | empty-misjudged {empty_err}")
    errs = Counter(r["count"] - r["truth"] for r in rows)
    print("error distribution (count-truth):", dict(sorted(errs.items())))
    by = {}
    for r in rows:
        by.setdefault(r["truth"], []).append(r["count"] == r["truth"])
    print("by truth:", {t: f"{sum(v)}/{len(v)}" for t, v in sorted(by.items())})
    if a.out:
        Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
