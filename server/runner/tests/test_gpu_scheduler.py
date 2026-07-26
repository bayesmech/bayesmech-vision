from __future__ import annotations

import threading
import time

from runner.gpu_scheduler import GreedyGpuScheduler


def test_greedy_gpu_scheduler_is_fifo_and_exclusive() -> None:
    scheduler = GreedyGpuScheduler()
    order: list[str] = []
    first_acquired = threading.Event()
    release_first = threading.Event()

    def run(name: str) -> None:
        with scheduler.lease("test", name):
            order.append(f"start:{name}")
            if name == "first":
                first_acquired.set()
                assert release_first.wait(timeout=2)
            time.sleep(0.01)
            order.append(f"end:{name}")

    first = threading.Thread(target=run, args=("first",))
    second = threading.Thread(target=run, args=("second",))
    third = threading.Thread(target=run, args=("third",))
    first.start()
    assert first_acquired.wait(timeout=2)
    second.start()
    time.sleep(0.01)
    third.start()

    deadline = time.monotonic() + 2
    while len(scheduler.snapshot()["queue"]) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    snapshot = scheduler.snapshot()
    assert snapshot["owner"]["task_id"] == "first"
    assert [item["task_id"] for item in snapshot["queue"]] == ["second", "third"]

    release_first.set()
    for thread in (first, second, third):
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert order == [
        "start:first",
        "end:first",
        "start:second",
        "end:second",
        "start:third",
        "end:third",
    ]
    assert scheduler.snapshot()["owner"] is None
    assert scheduler.snapshot()["queue"] == []
