"""Coalescing early-wakeup for periodic pollers.

A poller normally sleeps a fixed interval between iterations. When work lands
mid-interval (an observable is created, a delivery is queued) that fixed sleep is
pure latency: the row sits idle until the next tick. A ``Pulse`` lets the poller
``await pulse.wait(interval)`` instead of ``asyncio.sleep(interval)``; a producer
calls ``pulse.nudge()`` post-commit to wake it immediately.

The interval still elapses on its own when no nudge arrives, so the periodic drain
remains the correctness guarantee and the nudge is a pure latency optimisation —
durability, retry/backoff, and cron behaviour are unchanged.

Coalescing: multiple ``nudge()`` calls between waits collapse into one (the flag is
cleared each ``wait``), so a burst of writes triggers a single early drain, not a
thundering herd.

The underlying ``asyncio.Event`` is created lazily on the first ``wait()`` — i.e.
inside the poller's running loop — so importing this module never touches an event
loop, and a ``nudge()`` that races ahead of the poller (or fires from a test with a
different, short-lived loop) is a harmless no-op rather than a cross-loop error.
"""

import asyncio

__all__ = ["Pulse", "outbox_pulse", "push_pulse"]


class Pulse:
    def __init__(self) -> None:
        self._event: asyncio.Event | None = None

    def nudge(self) -> None:
        """Wake the poller now. No-op until the poller's first ``wait()`` has
        created the event (the poller will drain within one interval regardless)."""
        event = self._event
        if event is not None:
            event.set()

    async def wait(self, timeout: float) -> None:
        """Sleep up to ``timeout`` seconds, returning early if nudged."""
        if self._event is None:
            self._event = asyncio.Event()
        event = self._event
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except TimeoutError:
            pass
        event.clear()


#: Producers nudge these post-commit; the outbox and push pollers wait on them.
outbox_pulse = Pulse()
push_pulse = Pulse()
