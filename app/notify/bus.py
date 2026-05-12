from __future__ import annotations

import json
import queue
import threading
from typing import Any, Dict, Generator, List


class EventBus:
    """
    In-process pub/sub for SSE delivery.
    Subscribers register a Queue; publisher pushes to all queues.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queues: List[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=50)
        with self._lock:
            self._queues.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    def publish(self, event: Dict[str, Any]) -> None:
        data = json.dumps(event, default=str)
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass  # slow subscriber, drop


# Singleton used by server and internal notify endpoint
bus = EventBus()
