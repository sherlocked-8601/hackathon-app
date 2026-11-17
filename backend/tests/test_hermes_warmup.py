import asyncio
import json

import httpx
import pytest

from app.agent import hermes_runtime, readiness, warmup

CONFIG = hermes_runtime.HermesConfig('http://hermes:8642', 'synthetic-hermes-key')


def use_transport(monkeypatch, handler):
    monkeypatch.setattr(hermes_runtime, 'client',
                        lambda config: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_warmup_completes_one_tool_free_run(monkeypatch):
    sent = []
    def handler(request):
        if request.url.path == '/v1/runs':
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={'run_id': 'r1'})
        return httpx.Response(200, text='data: {"event": "run.completed", "output": "ok"}\n\n')
    use_transport(monkeypatch, handler)
    outcome, seconds = asyncio.run(warmup.warm(CONFIG))
    assert outcome == 'run.completed' and seconds >= 0
    assert sent[0]['session_id'].startswith('warmup-')
    assert 'Do not call any tools' in sent[0]['instructions']


def test_warmup_reports_failure_code_without_upstream_text(monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(500, text='PRIVATE-UPSTREAM-BODY'))
    outcome, _ = asyncio.run(warmup.warm(CONFIG))
    assert outcome == 'start_failed'


class Done(Exception):
    pass


def run_watch(states, *, gateway_ok=True, times=None, min_gap_s=600.0):
    states = list(states)
    clock = iter(times or range(0, 100000, 1000))
    warmed = []

    async def probe(config):
        if not states:
            raise Done
        return states.pop(0)

    async def gateway():
        return readiness.Readiness(gateway_ok, None if gateway_ok else 'gateway_exhausted')

    async def run(config):
        warmed.append(True)
        return 'run.completed', 0.1

    with pytest.raises(Done):
        asyncio.run(warmup.watch(CONFIG, poll_s=0, min_gap_s=min_gap_s, probe=probe,
                                 gateway=gateway, run=run, clock=lambda: next(clock)))
    return len(warmed)


def test_watch_warms_once_at_start_and_again_after_hermes_returns():
    assert run_watch([True, True, True, False, False, True, True]) == 2


def test_watch_waits_for_hermes_to_come_up_first():
    assert run_watch([False, False, True, True]) == 1


def test_watch_rate_limits_a_crash_loop():
    assert run_watch([True, False, True, False, True], times=[0, 10, 20], min_gap_s=600) == 1


def test_watch_skips_when_gateway_cannot_pay():
    assert run_watch([True, True], gateway_ok=False) == 0


def test_watch_survives_a_warmup_crash():
    states = [True, False, True]
    calls = []

    async def probe(config):
        if not states:
            raise Done
        return states.pop(0)

    async def gateway():
        return readiness.Readiness(True)

    async def run(config):
        calls.append(True)
        raise RuntimeError('boom')

    clock = iter([0, 1000, 1000])
    with pytest.raises(Done):
        asyncio.run(warmup.watch(CONFIG, poll_s=0, probe=probe, gateway=gateway, run=run,
                                 clock=lambda: next(clock)))
    assert len(calls) == 2


@pytest.mark.parametrize('flag, runtime, expected', [
    ('true', 'hermes', True), ('true', 'openrouter', False), ('', 'hermes', False)])
def test_auto_warmup_is_opt_in_and_hermes_only(monkeypatch, flag, runtime, expected):
    monkeypatch.setenv('HERMES_AUTO_WARMUP', flag)
    monkeypatch.setenv('CHAT_RUNTIME', runtime)
    assert warmup.auto_enabled() is expected


@pytest.mark.parametrize('value, expected', [(None, 600.0), ('120', 120.0), ('5', 60.0), ('soon', 600.0)])
def test_min_gap_never_drops_below_a_minute(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('HERMES_WARMUP_MIN_GAP_S', raising=False)
    else:
        monkeypatch.setenv('HERMES_WARMUP_MIN_GAP_S', value)
    assert warmup.min_gap_s() == expected


def test_warmup_cli_skips_when_hermes_is_not_configured(monkeypatch, capsys):
    monkeypatch.delenv('HERMES_API_URL', raising=False)
    assert warmup.main() == 2
    assert 'skipped' in capsys.readouterr().out
