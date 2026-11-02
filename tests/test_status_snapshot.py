"""status 不可從其他執行緒讀執行中的 MuJoCo，且模擬忙碌時要立刻回應。"""
import threading
import time

import pytest


@pytest.fixture(scope="module")
def service():
    from carearm.api.service import SimulationService

    return SimulationService(realtime=False)


def test_status_never_reads_the_simulation_off_the_worker(service, monkeypatch):
    reads = []
    original = service.sim.box_position

    def spy(period):
        reads.append(threading.current_thread())
        return original(period)

    monkeypatch.setattr(service.sim, "box_position", spy)
    status = service.status()
    assert set(status["boxes"]) == {"morning", "noon", "evening"}
    assert "tcp" in status and "gripper" in status
    assert all(thread is service.worker for thread in reads)


def test_status_answers_while_the_worker_is_occupied(service):
    release = threading.Event()
    job = service.submit(release.wait, 5, wait=False)
    try:
        started = time.monotonic()
        service.status()
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        job.result(timeout=5)


def test_snapshot_follows_the_simulation_after_a_job(service):
    service.submit(service._do_reset, service.sim.conditions)
    pos = service.submit(service.sim.box_position, "noon")
    assert service.status()["boxes"]["noon"]["sim_position"] == [round(float(v), 4) for v in pos]
