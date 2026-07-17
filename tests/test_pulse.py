"""Unit tests for the coalescing poller wakeup (app.core.pulse.Pulse)."""
import asyncio
import time

from app.core.pulse import Pulse


async def test_wait_returns_after_timeout_without_nudge():
    pulse = Pulse()
    start = time.monotonic()
    await pulse.wait(0.05)
    assert time.monotonic() - start >= 0.05


async def test_nudge_wakes_wait_early():
    pulse = Pulse()

    async def waiter():
        # Would sleep 5s on the fixed interval; a nudge should cut it short.
        start = time.monotonic()
        await pulse.wait(5.0)
        return time.monotonic() - start

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.02)  # let the waiter reach wait() and create the event
    pulse.nudge()
    elapsed = await asyncio.wait_for(task, timeout=1.0)
    assert elapsed < 1.0


async def test_nudge_before_wait_is_a_noop():
    # nudge() before the first wait() (event not yet created) is harmless; the next
    # wait still blocks for its full timeout rather than returning instantly.
    pulse = Pulse()
    pulse.nudge()
    start = time.monotonic()
    await pulse.wait(0.05)
    assert time.monotonic() - start >= 0.05


async def test_wait_clears_between_cycles():
    # A single nudge satisfies exactly one wait; the following wait blocks again.
    pulse = Pulse()
    await pulse.wait(0.01)  # create the event
    pulse.nudge()
    await pulse.wait(5.0)  # consumed by the nudge, returns fast
    start = time.monotonic()
    await pulse.wait(0.05)  # no pending nudge -> full timeout
    assert time.monotonic() - start >= 0.05
