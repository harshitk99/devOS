"""In-process pub/sub for live project updates (feeds the /ws endpoint).

Background executions run in worker threads while WebSocket handlers live on the event loop, so
`publish` is thread-safe and hands messages to each subscriber's loop. A short per-project
history is replayed to new subscribers so a page refresh doesn't lose the running log."""

import asyncio
import threading
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

HISTORY_LIMIT = 200


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[uuid.UUID, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = (
            defaultdict(list)
        )
        self._history: dict[uuid.UUID, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=HISTORY_LIMIT)
        )

    def publish(self, project_id: uuid.UUID, message: dict[str, Any]) -> None:
        message = {"ts": datetime.now(timezone.utc).isoformat(), **message}
        with self._lock:
            self._history[project_id].append(message)
            targets = list(self._subscribers[project_id])
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
            except RuntimeError:  # that loop has shut down; its handler will unsubscribe
                pass

    def subscribe(
        self, project_id: uuid.UUID, loop: asyncio.AbstractEventLoop
    ) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:  # snapshot + register atomically: no gap, no duplicate
            self._subscribers[project_id].append((loop, queue))
            return queue, list(self._history[project_id])

    def unsubscribe(self, project_id: uuid.UUID, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers[project_id] = [
                (loop, q) for loop, q in self._subscribers[project_id] if q is not queue
            ]


bus = EventBus()
