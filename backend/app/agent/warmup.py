"""One tiny Hermes run after Hermes starts, so the first owner does not pay its ~4s cold start.

Measured in the sandbox: a Hermes restart makes the next chat turn ~6.4s instead of ~2.5s, while an app
restart alone does not. /health does not help because the slow part is Hermes building its first agent run.
This costs one small model call through the same capped gateway as every chat turn."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid

import httpx

from . import hermes_runtime, readiness

INSTRUCTIONS = "Warm-up check. Reply with the single word ok. Do not call any tools."
POLL_S = 5.0
MIN_GAP_S = 600.0

logger = logging.getLogger(__name__)


def auto_enabled() -> bool:
    return (os.environ.get("HERMES_AUTO_WARMUP", "").strip().lower() in ("1", "true", "yes")
            and os.environ.get("CHAT_RUNTIME", "").strip().lower() == "hermes")


def min_gap_s() -> float:
    try:
        return max(60.0, float(os.environ.get("HERMES_WARMUP_MIN_GAP_S", MIN_GAP_S)))
    except ValueError:
        return MIN_GAP_S


async def hermes_up(config: hermes_runtime.HermesConfig) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            return (await http.get(config.url + "/health", headers=config.headers)).status_code < 400
    except httpx.HTTPError:
        return False


async def watch(config: hermes_runtime.HermesConfig, *, poll_s: float = POLL_S, min_gap_s: float = MIN_GAP_S,
                probe=hermes_up, gateway=readiness.gateway_ready, run=None, clock=time.monotonic) -> None:
    """Warm Hermes when the app starts and each time Hermes comes back after being down.

    At most one warm-up per min_gap_s, so a Hermes crash loop cannot keep spending. A Hermes restart
    faster than one poll is not seen; the first owner then pays the cold start, as before this existed."""
    run = run or warm
    was_up = False
    last = None
    while True:
        up = await probe(config)
        if up and not was_up:
            if last is not None and clock() - last < min_gap_s:
                logger.info("hermes_warmup skipped rate_limited")
            elif not (await gateway()).ok:
                logger.info("hermes_warmup skipped gateway_not_ready")
            else:
                last = clock()
                try:
                    outcome, seconds = await run(config)
                    logger.info("hermes_warmup %s in %.2fs", outcome, seconds)
                except Exception as exc:
                    logger.warning("hermes_warmup failed", extra={"failure_type": type(exc).__name__})
        was_up = up
        await asyncio.sleep(poll_s)


async def warm(config: hermes_runtime.HermesConfig) -> tuple[str, float]:
    started = time.monotonic()
    async with hermes_runtime.client(config) as http:
        run = hermes_runtime.HermesRun(config, http)
        try:
            await run.start(INSTRUCTIONS, "ok?", "warmup-" + uuid.uuid4().hex[:12])
            async for _ in run.events():
                pass
        except hermes_runtime.HermesError as exc:
            await run.stop()
            return str(exc), time.monotonic() - started
    return run.terminal or "no_terminal", time.monotonic() - started


def main() -> int:
    config = hermes_runtime.hermes_config()
    if config is None:
        print("warmup skipped: hermes not configured")
        return 2
    outcome, seconds = asyncio.run(warm(config))
    print(f"warmup {outcome} in {seconds:.2f}s")
    return 0 if outcome == "run.completed" else 1


if __name__ == "__main__":
    sys.exit(main())
