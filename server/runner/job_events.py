from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any


def _sortable_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            try:
                from datetime import datetime

                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
    return 0.0


class JobEventBroker:
    """In-process, resumable event stream for every runner-owned job."""

    def __init__(self, history_limit: int = 4096) -> None:
        self._condition = threading.Condition()
        self._revision = 0
        self._history: deque[dict[str, Any]] = deque(maxlen=max(128, history_limit))
        self._states: dict[str, dict[str, Any]] = {}

    def publish(self, state: dict[str, Any]) -> dict[str, Any]:
        job_id = str(state.get("job_id") or state.get("id") or "").strip()
        if not job_id:
            raise ValueError("job events require job_id or id")
        event = copy.deepcopy(state)
        event["job_id"] = job_id
        event.setdefault("id", job_id)
        event.setdefault("type", "runner")
        event.setdefault("title", str(event["type"]).replace("_", " ").title())
        event.setdefault("status", "queued")
        event.setdefault("stage", str(event["status"]))
        event.setdefault("message", "")
        progress = event.get("progress")
        if progress is None:
            progress = 1.0 if event["status"] in {"complete", "succeeded"} else 0.0
        try:
            event["progress"] = max(0.0, min(1.0, float(progress)))
        except (TypeError, ValueError):
            event["progress"] = 0.0
        event.setdefault("created_at", time.time())
        event["updated_at"] = event.get("updated_at") or time.time()

        with self._condition:
            self._revision += 1
            event["revision"] = self._revision
            self._states[job_id] = event
            self._history.append(event)
            self._condition.notify_all()
        return copy.deepcopy(event)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._condition:
            states = [copy.deepcopy(state) for state in self._states.values()]
        states.sort(
            key=lambda state: _sortable_time(
                state.get("updated_at") or state.get("created_at")
            ),
            reverse=True,
        )
        return states

    def events_after(self, revision: int) -> list[dict[str, Any]]:
        with self._condition:
            return [
                copy.deepcopy(event)
                for event in self._history
                if int(event.get("revision") or 0) > revision
            ]

    def wait_for_events(
        self, revision: int, timeout: float = 15.0
    ) -> list[dict[str, Any]]:
        with self._condition:
            events = [
                copy.deepcopy(event)
                for event in self._history
                if int(event.get("revision") or 0) > revision
            ]
            if events:
                return events
            self._condition.wait(timeout=max(0.0, timeout))
            return [
                copy.deepcopy(event)
                for event in self._history
                if int(event.get("revision") or 0) > revision
            ]

    def clear(self) -> None:
        """Reset broker state. Intended for isolated tests."""

        with self._condition:
            self._revision = 0
            self._history.clear()
            self._states.clear()
            self._condition.notify_all()


job_events = JobEventBroker()


def publish_runner_job(state: dict[str, Any]) -> dict[str, Any]:
    status = str(state.get("status") or "queued")
    job_type = str(state.get("type") or "runner")
    progress = state.get("progress")
    if progress is None:
        if status in {"succeeded", "complete"}:
            progress = 1.0
        elif status == "running":
            progress = 0.05
        else:
            progress = 0.0
    return job_events.publish(
        {
            **state,
            "job_id": str(state.get("id") or state.get("job_id") or ""),
            "type": job_type,
            "title": str(state.get("title") or job_type.replace("_", " ").title()),
            "source": str(state.get("source") or "runner"),
            "progress": progress,
        }
    )
