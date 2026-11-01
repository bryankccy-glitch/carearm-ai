"""命令列示範：完整一次「語音指令 → 視覺 → 排程 → 決策 → 夾取」。

    python -m carearm.demo --text "幫我拿中午的藥" --time 12:10
    mjpython -m carearm.demo --text "我要吃藥了" --time 18:30 --viewer      # macOS 開 3D 視窗
    python -m carearm.demo --period evening --randomize 3                  # 直接選時段、隨機擺放
    mjpython -m carearm.demo --period noon --viewer --confirm --then-finish # 取藥後再模擬「我吃完藥了」把空盒收到回收區
"""

from __future__ import annotations

import argparse
import random
import time

import cv2

from carearm.decision.engine import CONFIRM, EXECUTE, CalibratedRisk, Observation, RiskModel, collect_decision, is_finish_command
from carearm.experiments.scenarios import sample_positions
from carearm.perception.calibration import load_detector
from carearm.perception.detector import annotate
from carearm.pipeline import interpret
from carearm.robot.executor import RetrievalExecutor
from carearm.settings import PERIOD_ZH, RESULTS_DIR, load_config
from carearm.simulation.scene import SceneConditions, make_carearm_env
from carearm.speech.intent import IntentClassifier


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", default=None, help="中文指令（文字模擬語音轉錄）")
    parser.add_argument("--period", choices=("morning", "noon", "evening"), default=None)
    parser.add_argument("--time", default="12:10", help="模擬時間 HH:MM")
    parser.add_argument("--randomize", type=int, default=None, metavar="SEED", help="隨機擺放藥盒")
    parser.add_argument("--viewer", action="store_true", help="開 MuJoCo 原生視窗（macOS 用 mjpython）")
    parser.add_argument("--confirm", action="store_true", help="若系統請求確認，以照護者身分確認")
    parser.add_argument("--then-finish", action="store_true", help="取藥成功後，再說「我吃完藥了」把空盒移到回收區")
    args = parser.parse_args()
    text = args.text or (f"幫我拿{PERIOD_ZH[args.period]}的藥" if args.period else "我要吃藥了")

    cfg = load_config()
    env = make_carearm_env(cfg)
    sim = env.unwrapped
    cond = SceneConditions()
    if args.randomize is not None:
        cond.positions = sample_positions(random.Random(args.randomize), cfg, True)
    env.reset(seed=0, options={"conditions": cond})

    viewer = None
    if args.viewer:
        import mujoco.viewer

        viewer = mujoco.viewer.launch_passive(sim.model, sim.data)

    hook = (lambda: viewer.sync()) if viewer else None
    executor = RetrievalExecutor(env, cfg, step_hook=hook, realtime=bool(viewer), status_cb=lambda s: print(f"  · {s}"))
    executor.move_to_observation_pose()
    image = sim.render_overhead()
    detections = load_detector(cfg).detect(image, sim.camera_geometry())
    from carearm.decision.schedule import to_minutes

    intent, sched = interpret(text, to_minutes(args.time), [], IntentClassifier.load(), cfg)
    try:
        model = RiskModel.load()
    except Exception:
        model = None
    decision = CalibratedRisk(cfg, model).decide(Observation(intent, sched, detections))

    RESULTS_DIR.mkdir(exist_ok=True)
    target = detections[decision.target_index] if decision.target_index is not None else None
    cv2.imwrite(str(RESULTS_DIR / "demo_camera.png"), cv2.cvtColor(annotate(image, detections, target), cv2.COLOR_RGB2BGR))
    print(f"指令：{text}（{args.time}）→ 意圖 {intent.intent}，校正信心 {intent.confidence:.3f}")
    for d in detections:
        print(f"視覺：{d.predicted_period:8s} {d.dot_count} 點  機率 {d.calibrated_probs.get(d.predicted_period, 0):.3f}  "
              f"世界座標 ({d.world_xy[0]:.3f}, {d.world_xy[1]:.3f})")
    for r in decision.rules:
        print(f"規則：{'通過' if r.passed else '阻擋'}  {r.message}")
    print(f"決策：{decision.decision}" + (f"（風險 {decision.risk:.3f}）" if decision.risk is not None else ""))
    for reason in decision.reasons:
        print(f"  - {reason}")

    status = 0
    allowed = decision.decision == EXECUTE or (
        decision.decision == CONFIRM and args.confirm and all(r.passed for r in decision.rules) and decision.target_xy
    )
    if allowed:
        result = executor.execute(decision.target_period, decision.target_xy)
        print("=" * 50)
        print(f"結果：{'成功' if result.success else '失敗'}｜抬起 {result.lifted_box}｜送達 {result.delivered_box}"
              f"｜{result.sim_steps} 控制步（{result.sim_steps * sim.control_dt:.1f} 模擬秒）")
        if result.error:
            print(f"原因：{result.error}")
        status = 0 if result.success else 1
        if result.success and args.then_finish:
            print("=" * 50)
            finish = IntentClassifier.load().predict("我吃完藥了")
            print(f"指令：我吃完藥了 → {finish.intent}（回收指令：{is_finish_command(finish)}）")
            executor.move_to_observation_pose()
            delivered = [d for d in load_detector(cfg).detect_all(sim.render_overhead(), sim.camera_geometry()) if d.zone == "delivery"]
            collect = collect_decision(delivered, cfg)
            for r in collect.rules:
                print(f"規則：{'通過' if r.passed else '阻擋'}  {r.message}")
            print(f"決策：{collect.decision}")
            if collect.decision == EXECUTE:
                area = cfg["collection_area"]
                back = executor.execute(collect.target_period, collect.target_xy, place_area="collection",
                                        place_xy=(area["center"][0] + area["slot_offsets_x"][0], area["center"][1]))
                print(f"回收結果：{'成功' if back.success else '失敗'}｜抬起 {back.lifted_box}｜放入回收區 {back.delivered_box}")
                status = 0 if back.success else 1
    else:
        print("未執行手臂動作。" + ("（可加 --confirm 以照護者身分確認）" if decision.decision == CONFIRM else ""))

    if viewer:
        end = time.time() + 8
        while viewer.is_running() and time.time() < end:
            viewer.sync()
            time.sleep(0.02)
        viewer.close()
    env.close()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
