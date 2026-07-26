from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class GpuLease:
    ticket_id: str
    task_type: str
    task_id: str
    queued_at: float
    acquired_at: float


class GreedyGpuScheduler:
    """A process-local FIFO lease for the runner's single GPU.

    The policy is deliberately small: enqueue at the tail, let the oldest
    waiter take the GPU as soon as it is free, and never preempt an owner.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._queue: deque[tuple[str, str, str, float]] = deque()
        self._owner: GpuLease | None = None

    def acquire(
        self,
        task_type: str,
        task_id: str = "",
        *,
        on_wait: Callable[[int], None] | None = None,
    ) -> GpuLease:
        ticket_id = f"gpu-{uuid.uuid4().hex[:12]}"
        queued_at = time.time()
        normalized_id = task_id or ticket_id
        ticket = (ticket_id, task_type, normalized_id, queued_at)
        with self._condition:
            self._queue.append(ticket)
            if on_wait is not None:
                on_wait(len(self._queue) - 1 + (1 if self._owner else 0))
            while self._owner is not None or self._queue[0][0] != ticket_id:
                self._condition.wait()
            self._queue.popleft()
            lease = GpuLease(
                ticket_id=ticket_id,
                task_type=task_type,
                task_id=normalized_id,
                queued_at=queued_at,
                acquired_at=time.time(),
            )
            self._owner = lease
            return lease

    def release(self, lease: GpuLease) -> None:
        with self._condition:
            if self._owner is None or self._owner.ticket_id != lease.ticket_id:
                raise RuntimeError("GPU lease is not owned by this task")
            self._owner = None
            self._condition.notify_all()

    @contextmanager
    def lease(
        self,
        task_type: str,
        task_id: str = "",
        *,
        on_wait: Callable[[int], None] | None = None,
    ) -> Iterator[GpuLease]:
        acquired = self.acquire(task_type, task_id, on_wait=on_wait)
        try:
            yield acquired
        finally:
            self.release(acquired)

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            owner = self._owner
            queue = list(self._queue)
        return {
            "policy": "greedy-fifo",
            "owner": (
                {
                    "ticket_id": owner.ticket_id,
                    "task_type": owner.task_type,
                    "task_id": owner.task_id,
                    "queued_at": owner.queued_at,
                    "acquired_at": owner.acquired_at,
                }
                if owner
                else None
            ),
            "queue": [
                {
                    "ticket_id": ticket_id,
                    "task_type": task_type,
                    "task_id": task_id,
                    "queued_at": queued_at,
                    "position": index + 1,
                }
                for index, (ticket_id, task_type, task_id, queued_at) in enumerate(
                    queue
                )
            ],
        }


gpu_scheduler = GreedyGpuScheduler()
