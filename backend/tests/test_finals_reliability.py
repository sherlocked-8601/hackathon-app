"""User-visible finals regressions with scripted providers and isolated ledgers."""
import json
from types import SimpleNamespace

import pytest

from app import auto_verification, chat, verification_store as store, verification_worker as worker
from app.agent import readiness
from app.model_gateway import Ledger
from app.request_semantics import Budget, Fact, RequestState, excluded_task_ids
from app.schemas import BusinessProfile
from test_chat_hermes import FakeHermes, answer, hermes
from test_conversation_journeys import public_reads, turn
from test_semantic_state_api import fact, reply, scripted
from test_verification_pipeline import connection


def said(events):
    return ' '.join(event['text'] for event in events if event['type'] == 'message_delta')


def test_invalid_followup_has_text_and_keeps_prior_exclusions(client, hermes):
    fake = hermes(FakeHermes(lambda package: answer(package, tools=(), output='{"wrong":true}')))
    events, memory = turn(client, 'What can you do for this instead?',
                          {'business_description': 'tutoring', 'background_done': True},
                          [{'role': 'user', 'text': "I do not need bookkeeping, I need something else"}])
    assert fake.requests
    assert "couldn't validate" in said(events)
    assert 'bookkeeping' in memory['excluded_task_ids']
    assert 'bookkeeping' not in memory['pending_task_ids']


def test_exclusion_survives_no_history_outage_and_can_be_withdrawn(client, hermes, public_reads, monkeypatch):
    monkeypatch.setenv('CHAT_BUDGET_AUTHORITY', 'gateway')
    hermes(FakeHermes(answer))
    async def down():
        return readiness.Readiness(False, 'hermes_unreachable')
    monkeypatch.setattr(readiness, 'check', down)
    _, memory = turn(client, 'What else could help?', {'business_description': 'tutoring',
                      'background_done': True, 'excluded_task_ids': ['bookkeeping']})
    assert 'bookkeeping' not in memory['pending_task_ids']
    _, updated = turn(client, 'Actually I need bookkeeping now', memory)
    assert 'bookkeeping' not in updated['excluded_task_ids']


def test_all_excluded_returns_explanation_without_reintroducing_choices(client, hermes, public_reads, monkeypatch):
    monkeypatch.setenv('CHAT_BUDGET_AUTHORITY', 'gateway')
    hermes(FakeHermes(answer))
    async def down():
        return readiness.Readiness(False, 'hermes_unreachable')
    monkeypatch.setattr(readiness, 'check', down)
    events, _ = turn(client, 'What else can help my business?', {'business_description': 'tutoring',
        'background_done': True, 'excluded_task_ids': [c.id for c in client.app.state.catalog.problem_cards]})
    assert "couldn't find a suitable task" in said(events)
    assert not [event for event in events if event['type'] == 'clarify']


def test_dispatch_does_not_recommend_appointment_apps(client, public_reads):
    events, memory = turn(client, 'I run an HVAC business and need to dispatch technicians')
    assert 'field_dispatch' in memory['active_task_ids']
    assert not set(memory['last_tool_ids']) & {'calendly', 'setmore', 'acuity'}
    assert any(e['type'] == 'recommendation' and e['problem_id'] == 'field_dispatch' for e in events)


def test_valid_selection_accepts_consumed_semantics_field(client, hermes):
    hermes(FakeHermes(lambda package: answer(package, output=json.dumps({
        'claim_ids': [], 'followup_id': None, 'semantics': None}))))
    events, _ = turn(client, 'I need bookkeeping', {'trade': 'hvac'})
    assert any(e.get('validation') == 'validated' for e in events)
    assert not any(e.get('validation') == 'fallback' for e in events)


def test_slow_success_is_not_cancelled_at_twelve_seconds(client, hermes, monkeypatch):
    fake = hermes(FakeHermes(lambda package: answer(package, tools=(), output=json.dumps(
        reply('I need help reaching more people', None, []))), delay=7))
    monkeypatch.setenv('HERMES_TIMEOUT_SECONDS', '35')
    async def ready():
        return readiness.Readiness(True)
    monkeypatch.setattr(readiness, 'check_now', ready)
    events, _ = turn(client, 'I need help reaching more people', {'business_description': 'tutoring'})
    assert said(events) and 'unavailable' not in said(events)
    assert len([r for r in fake.requests if r[1] == '/v1/runs']) == 1
    assert not [r for r in fake.requests if r[1].endswith('/stop')]


def test_semantic_followup_enforces_stored_budget_and_task(client, hermes, public_reads, monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    message = 'I need appointment booking, not bookkeeping'
    ops = [fact('deliverable', 'appointment booking', 'appointment_booking'),
           fact('task_exclusion', 'not bookkeeping', 'bookkeeping')]
    scripted(hermes, reply(message, {'ops': ops}, ['appointment_booking']))
    _, memory = turn(client, message, {'business_description': 'tutoring', 'background_done': True})
    follow = 'My limit is USD 10 per month total'
    budget = fact('budget', follow, None)
    budget['fact']['budget'] = {'amount': 10, 'currency': 'USD', 'period': 'month', 'basis': 'total'}
    raw = reply(follow, {'ops': [budget]}, ['appointment_booking'])
    raw['plan']['steps'][0].update(kind='recommend', reply=None)
    scripted(hermes, raw)
    events, memory = turn(client, follow, memory)
    recommendations = [e for e in events if e['type'] == 'recommendation']
    assert recommendations and recommendations[0]['problem_id'] == 'appointment_booking'
    assert recommendations[0]['profile']['budget_monthly_usd'] == 10
    assert 'bookkeeping' not in memory['active_task_ids']
    third = 'Please show those options again'
    raw = reply(third, {'ops': []}, ['appointment_booking'])
    raw['plan']['steps'][0].update(kind='recommend', reply=None)
    scripted(hermes, raw)
    events, _ = turn(client, third, memory)
    assert any(e['type'] == 'recommendation' and e['profile']['budget_monthly_usd'] == 10 for e in events)


def test_hard_zero_budget_and_soft_preference_are_distinct(monkeypatch, catalog):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    state = RequestState(facts=[
        Fact(id='f1', turn=1, kind='budget', strength='hard', scope='business', evidence='only free',
             budget=Budget(amount=0, currency='USD', period='month', basis='total')),
        Fact(id='f2', turn=1, kind='task_exclusion', strength='preference', scope='business', evidence='prefer not books', task_id='books')])
    assert excluded_task_ids(state) == set()
    assert chat._effective_profile(catalog, BusinessProfile(), chat.ChatMemory(requirements=state)).budget_monthly_usd == 0


def test_budget_recovery_claim_is_immediate_and_exclusive(connection):
    lease = store.schedule_claim(connection, 'discovery', now=100)
    store.schedule_finish(connection, 'discovery', lease, 'budget_paused', now=101, interval=86400)
    assert store.schedule_claim(connection, 'discovery', now=102) is None
    resumed = store.schedule_claim(connection, 'discovery', now=103, resume_budget=True)
    assert resumed
    assert store.schedule_claim(connection, 'discovery', now=104, resume_budget=True) is None
    store.schedule_finish(connection, 'discovery', resumed, 'completed', now=105)
    assert store.schedule_claim(connection, 'discovery', now=106, resume_budget=True) is None


@pytest.mark.parametrize('reason', ['requests', 'money'])
def test_review_cannot_spend_last_discovery_reservation(tmp_path, monkeypatch, reason):
    monkeypatch.setenv('SCHEDULED_DISCOVERY_ENABLED', 'true')
    ledger = Ledger(tmp_path / 'spend.db')
    search = SimpleNamespace(max_requests=2, cap_usd=1.0, ledger=ledger)
    number, _ = ledger.admit(0.8 if reason == 'money' else 0.01, 2, 1)
    ledger.settle(number, 0.74 if reason == 'money' else 0.01)
    if reason == 'money':
        search.max_requests = 100
    limits = auto_verification.review_limits(search)
    assert ledger.admit(0.02, *limits)[0] is None
    assert ledger.admit(0.25, search.max_requests, search.cap_usd)[0] is not None


def test_failed_budget_check_retries_without_daily_delay(connection, monkeypatch):
    monkeypatch.setenv('SCHEDULED_DISCOVERY_ENABLED', 'true')
    finder = SimpleNamespace(available=lambda: False)
    assert worker.scheduled_discovery(connection, finder, now=100) == 'budget_paused'
    assert store.status(connection)['scheduled_discovery']['next_due'] == 1000


def test_incomplete_review_waits_for_refresh_not_fifteen_minutes(connection):
    store.enqueue(connection, store.source_tools(connection), now=10)
    job = store.claim(connection, now=11)
    assert store.finish(connection, job, outcome='partial', now=12)
    due = connection.execute('SELECT due FROM verification_job WHERE tool_id=? AND capability=?',
                             (job['tool_id'], job['capability'])).fetchone()[0]
    assert due >= 86412


def test_negation_and_currency_must_not_disappear_from_vendor_quotes():
    item = auto_verification.Evidence(value=True, url='https://vendor.test', quote='Starter plan offers unlimited invoices')
    assert not auto_verification.quoted(item, {'https://vendor.test': 'Starter plan never offers unlimited invoices'})


def test_unresolved_task_scope_binds_when_task_becomes_known():
    from app.request_semantics import bind_active_task, start_turn, excluded_tool_ids
    state = RequestState(facts=[Fact(id='f1', turn=1, kind='product_exclusion', strength='hard',
                        scope='active_task', evidence='not that product', tool_ids=['example'])])
    assert excluded_tool_ids(start_turn(state, [])) == {'example'}
    state = bind_active_task(state, ['bookkeeping'])
    assert excluded_tool_ids(start_turn(state, ['bookkeeping'])) == {'example'}
    assert not excluded_tool_ids(start_turn(state, ['invoicing']))


def test_owned_excluded_product_cannot_reenter_filtered_context(catalog):
    from app.penny_grounding import filtered_context
    memory = chat.ChatMemory(owned=['ledger'], ruled_out=['ledger'])
    clean, profile = filtered_context(catalog, BusinessProfile(), memory)
    assert not chat._effective_profile(clean, profile, memory).owned


def test_semantic_outage_still_honors_current_explicit_refusal(client, monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    body = chat.ChatRequest(conversation_id='degraded', message='I do not need bookkeeping')
    assert 'bookkeeping' in chat._turned_down(client.app.state.catalog, body)


@pytest.mark.parametrize('search_status, expected_code, delay', [
    ('completed', 'completed', 86400), ('failed', 'search_unavailable', 900)])
def test_recovered_discovery_runs_once_and_failed_search_is_not_daily_success(connection, monkeypatch, search_status, expected_code, delay):
    from app import discovery
    monkeypatch.setenv('SCHEDULED_DISCOVERY_ENABLED', 'true')
    lease = store.schedule_claim(connection, 'discovery', now=100)
    store.schedule_finish(connection, 'discovery', lease, 'budget_paused', now=101)
    calls = []
    def find(catalog, request, finder):
        calls.append(request)
        return discovery.FinderResult(contract_version='1.1.0', run_id='bounded-sandbox-search',
                                      status=search_status, provenance='live')
    monkeypatch.setattr(discovery, 'run_finder', find)
    finder = SimpleNamespace(available=lambda: True)
    assert worker.scheduled_discovery(connection, finder, now=102) == expected_code
    assert len(calls) == 1 and calls[0].max_results == 2
    assert store.status(connection)['scheduled_discovery']['next_due'] == 102 + delay
    assert worker.scheduled_discovery(connection, finder, now=103) is None
    assert len(calls) == 1
