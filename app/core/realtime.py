"""
Realtime fan-out layer for live features (Q&A, polls, reactions).

A single `broadcaster` publishes JSON messages on named channels and lets SSE
endpoints subscribe to them. Two modes:

- **Redis** (when REDIS_URL is set): publishes go to Redis pub/sub and a reader
  task fans them back out to local subscribers, so multiple workers/containers
  all see every message.
- **In-process** (default): publishes are delivered straight to local
  subscribers in the same process. Perfect for a single worker and tests.

Either way the public API is identical, so endpoints don't care which is active.
"""
import asyncio
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Set

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class Broadcaster:
    def __init__(self) -> None:
        self._local: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._redis = None
        self._reader_task = None
        self._connected = False

    async def connect(self) -> None:
        """Wire up Redis if configured; otherwise stay in in-process mode."""
        url = settings.REDIS_URL
        if not url:
            return
        try:
            import redis.asyncio as aioredis  # lazy: optional dependency
            self._redis = aioredis.from_url(url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
            self._reader_task = asyncio.create_task(self._reader())
            logger.info("Realtime broadcaster connected to Redis")
        except Exception as e:  # noqa: BLE001 - degrade to in-process
            logger.warning("Realtime: Redis unavailable (%s); using in-process mode", e)
            self._redis = None
            self._connected = False

    async def disconnect(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._redis = None
        self._connected = False

    async def _reader(self) -> None:
        """Pump Redis pub/sub messages to local subscribers.

        Wrapped in a reconnect loop: a transient Redis error used to kill the
        reader permanently (publishes kept 'succeeding' but nothing was ever
        delivered), so cross-worker SSE fan-out went silently dead until a
        restart. Now it backs off and re-subscribes instead.
        """
        backoff = 1
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.psubscribe("rt:*")
                backoff = 1  # healthy again
                async for msg in pubsub.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    self._dispatch_local(msg["channel"], msg["data"])
            except asyncio.CancelledError:
                try:
                    await pubsub.aclose()
                except Exception:  # noqa: BLE001
                    pass
                raise
            except Exception as e:  # noqa: BLE001 - reconnect instead of dying
                logger.warning("Realtime reader error (%s); reconnecting in %ss", e, backoff)
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2, 30)

    def _dispatch_local(self, channel: str, raw: str) -> None:
        for q in list(self._local.get(channel, ())):
            try:
                q.put_nowait(raw)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                pass

    async def publish(self, channel: str, data: dict) -> None:
        raw = json.dumps(data)
        if self._connected and self._redis is not None:
            try:
                await self._redis.publish(channel, raw)
                return  # local subscribers receive it via _reader
            except Exception as e:  # noqa: BLE001 - fall back to local delivery
                logger.warning("Realtime publish to Redis failed: %s", e)
        self._dispatch_local(channel, raw)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._local[channel].add(q)
        try:
            yield q
        finally:
            self._local[channel].discard(q)
            if not self._local[channel]:
                self._local.pop(channel, None)


broadcaster = Broadcaster()


def qa_channel(session_id: int) -> str:
    return f"rt:qa:{session_id}"


def poll_channel(session_id: int) -> str:
    return f"rt:poll:{session_id}"


def react_channel(session_id: int) -> str:
    return f"rt:react:{session_id}"
