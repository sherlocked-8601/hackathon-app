"""The semantic state flag end to end through /api/chat, with a scripted Hermes; no paid calls."""
import json

import pytest

from app.request_semantics import SEMANTICS_MARKER
from test_chat_hermes import FakeHermes, answer, hermes
from test_conversation_journeys import turn

MESSAGE = "I don't need help with reviews, just scheduling"


@pytest.fixture
def task_pair(client):
    from app.main import app
    tasks = [card.id for card in app.state.catalog.problem_cards]
    return next(t for t in tasks if 'review' in t), next(t for t in tasks if 'schedul' in t)


def fact(kind, evidence, task_id):
    return {'op': 'add', 'target_id': None, 'evidence': evidence,
            'fact': {'kind': kind, 'strength': 'hard', 'scope': 'business', 'task_id': task_id,
                     'tool_ids': [], 'budget': None, 'evidence': evidence}}


def reply(message, semantics, problem_ids):
    step = {'kind': 'clarify', 'owner_text': message, 'topic': 'workflow', 'criterion': None, 'presentation': None,
            'tool_ids': [], 'exclude_tool_ids': [], 'problem_ids': problem_ids, 'context_mode': 'owner',
            'assumed_business': None, 'reply': 'Got it. Which part of scheduling is hardest right now?'}
    return {'claim_ids': [], 'followup_id': None, 'routes': None, 'trade': None, 'trade_text': None,
            'action': None, 'conversation_intent': None, 'reply_id': None, 'business_text': None,
            'plan': {'steps': [step]}, 'semantics': semantics}


def scripted(hermes, raw):
    return hermes(FakeHermes(lambda package: answer(package, tools=(), output=json.dumps(raw))))


def sent_prompt(fake):
    return json.dumps([body for _, _, body, _ in fake.requests])


def test_flag_off_keeps_the_old_prompt_and_ignores_semantics(client, hermes, task_pair, monkeypatch):
    monkeypatch.delenv('CHAT_SEMANTIC_STATE', raising=False)
    reviews, scheduling = task_pair
    fake = scripted(hermes, reply(MESSAGE, {'ops': [fact('task_exclusion', "I don't need help with reviews", reviews)],
                                            'unresolved': [], 'ambiguities': []}, [scheduling]))
    _, memory = turn(client, MESSAGE)
    assert SEMANTICS_MARKER not in sent_prompt(fake)
    assert not memory.get('requirements', {}).get('facts')


def test_flag_on_stores_the_refusal_and_the_wanted_task(client, hermes, task_pair, monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    reviews, scheduling = task_pair
    semantics = {'ops': [fact('task_exclusion', "I don't need help with reviews", reviews),
                         fact('deliverable', 'just scheduling', scheduling)],
                 'unresolved': [], 'ambiguities': []}
    fake = scripted(hermes, reply(MESSAGE, semantics, [scheduling]))
    _, memory = turn(client, MESSAGE)
    assert SEMANTICS_MARKER in sent_prompt(fake)
    stored = {(item['kind'], item['task_id']) for item in memory['requirements']['facts']}
    assert stored == {('task_exclusion', reviews), ('deliverable', scheduling)}

    follow = 'mostly the booking part'
    fake = scripted(hermes, reply(follow, {'ops': [], 'unresolved': [], 'ambiguities': []}, [scheduling]))
    _, later = turn(client, follow, memory)
    assert "I don't need help with reviews" in sent_prompt(fake)
    assert {(item['kind'], item['task_id']) for item in later['requirements']['facts']} == stored


def test_a_malformed_delta_leaves_state_alone_and_the_turn_still_answers(client, hermes, task_pair, monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    _, scheduling = task_pair
    scripted(hermes, reply(MESSAGE, {'ops': [{'op': 'explode'}]}, [scheduling]))
    events, memory = turn(client, MESSAGE)
    assert any(event['type'] == 'clarify' for event in events)
    assert not memory.get('requirements', {}).get('facts')


def test_evidence_the_owner_did_not_say_is_not_stored(client, hermes, task_pair, monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    reviews, scheduling = task_pair
    scripted(hermes, reply(MESSAGE, {'ops': [fact('task_exclusion', 'never do reviews', reviews)],
                                     'unresolved': [], 'ambiguities': []}, [scheduling]))
    _, memory = turn(client, MESSAGE)
    assert not memory.get('requirements', {}).get('facts')


@pytest.mark.parametrize('message, social, arithmetic, tapped, skip', [
    ('hi', True, False, False, True),
    ('thanks', True, False, False, True),
    ('thanks but not reviews', True, False, False, False),
    ('hi, just scheduling', True, False, False, False),
    ('hello and compare prices', True, False, False, False),
    ('hi under 50', True, False, False, False),
    ('12 * 3', False, True, False, True),
    ('The register does not do enough', False, False, True, True),
    ('I keep missing calls', False, False, False, False),
])
def test_only_a_fully_understood_message_skips_hermes(message, social, arithmetic, tapped, skip):
    from app.request_semantics import can_skip_hermes
    assert can_skip_hermes(message, social=social, arithmetic=arithmetic, tapped=tapped) is skip


@pytest.fixture
def semantic(monkeypatch):
    monkeypatch.setenv('CHAT_SEMANTIC_STATE', 'true')
    monkeypatch.setenv('CHAT_CATALOG_FAST_PATH', 'true')


def test_a_refusal_with_a_request_goes_to_hermes_with_full_context_only(client, hermes, task_pair, semantic):
    _, scheduling = task_pair
    fake = scripted(hermes, reply(MESSAGE, {'ops': [], 'unresolved': [], 'ambiguities': []}, [scheduling]))
    turn(client, MESSAGE)
    prompt = sent_prompt(fake)
    assert fake.requests and SEMANTICS_MARKER in prompt
    assert 'routing_excerpts' not in prompt and 'initial_evidence_package' not in prompt


def test_the_same_message_keeps_the_keyword_route_when_the_flag_is_off(client, hermes, task_pair, monkeypatch):
    monkeypatch.delenv('CHAT_SEMANTIC_STATE', raising=False)
    monkeypatch.setenv('CHAT_CATALOG_FAST_PATH', 'true')
    _, scheduling = task_pair
    fake = scripted(hermes, reply(MESSAGE, None, [scheduling]))
    turn(client, MESSAGE)
    assert 'routing_excerpts' in sent_prompt(fake) or not fake.requests


@pytest.mark.parametrize('message', ['hi', 'thanks', '12 * 3'])
def test_a_greeting_or_sum_never_calls_hermes(client, hermes, task_pair, semantic, message):
    fake = scripted(hermes, reply(message, None, []))
    events, _ = turn(client, message)
    assert not fake.requests
    assert events[-1]['type'] == 'done' and not any(event['type'] == 'error' for event in events)


@pytest.mark.parametrize('message', ['thanks but not reviews', 'hi, I need scheduling'])
def test_a_greeting_with_anything_else_goes_to_hermes(client, hermes, task_pair, semantic, message):
    _, scheduling = task_pair
    fake = scripted(hermes, reply(message, {'ops': [], 'unresolved': [], 'ambiguities': []}, [scheduling]))
    turn(client, message)
    assert fake.requests


def events_for(client, message, memory=None):
    from test_conversation_journeys import frames
    response = client.post('/api/chat', json={'conversation_id': 'journey', 'message': message,
                                              'memory': memory or {}, 'history': []})
    return [(event['type'], event.get('code')) for event in frames(response)]


def test_a_tapped_task_card_skips_hermes_and_matches_the_flag_off_route(client, hermes, task_pair, monkeypatch):
    from app.main import app
    _, scheduling = task_pair
    title = next(card.title for card in app.state.catalog.problem_cards if card.id == scheduling)
    monkeypatch.setenv('CHAT_CATALOG_FAST_PATH', 'true')
    results = []
    for flag in ('', 'true'):
        monkeypatch.setenv('CHAT_SEMANTIC_STATE', flag)
        fake = scripted(hermes, reply(title, None, [scheduling]))
        results.append(events_for(client, title))
        assert not fake.requests
    assert results[0] == results[1]


def test_hermes_down_falls_back_to_the_flag_off_route(client, hermes, task_pair, monkeypatch):
    from app.agent import readiness
    _, scheduling = task_pair

    async def down():
        return readiness.Readiness(False, 'mcp_unreachable')
    monkeypatch.setenv('CHAT_CATALOG_FAST_PATH', 'true')
    results = []
    for flag in ('', 'true'):
        monkeypatch.setenv('CHAT_SEMANTIC_STATE', flag)
        fake = scripted(hermes, reply('hi', None, []))
        monkeypatch.setattr(readiness, 'check', down)
        results.append(events_for(client, MESSAGE))
        assert not [request for request in fake.requests if request[1] == '/v1/runs']
    assert results[0] == results[1]


def social_reply(message, topic):
    raw = reply(message, {'ops': [], 'unresolved': [], 'ambiguities': []}, [])
    raw['plan']['steps'][0].update(kind='social', topic=topic, reply=None)
    return raw


def learned(client):
    from app.main import app
    return app.state.conn, app.state.lock, app.state.catalog


def test_a_phrase_is_reused_only_after_three_different_people_agree(client):
    from app import learned_social
    from app.conversation_plan import Plan, Step
    conn, lock, catalog = learned(client)
    plan = Plan(steps=[Step(kind='social', owner_text='see ya', topic='goodbye')])
    for person in ('a', 'a', 'b'):
        learned_social.observe(conn, lock, 'See ya!', catalog, plan, person)
    assert learned_social.lookup(conn, lock, 'see ya', catalog) is None
    learned_social.observe(conn, lock, 'see ya', catalog, plan, 'c')
    assert learned_social.lookup(conn, lock, 'See ya.', catalog) == 'goodbye'
    learned_social.observe(conn, lock, 'see ya', catalog, None, 'd')
    assert learned_social.lookup(conn, lock, 'see ya', catalog) is None


@pytest.mark.parametrize('message', ['bye but not yet', 'bye 2', 'cya, compare prices', 'thanks for the tools',
                                     'one two three four five', 'bye and thanks', 'which one'])
def test_risky_phrases_are_never_learned(client, message):
    from app import learned_social
    from app.conversation_plan import Plan, Step
    conn, lock, catalog = learned(client)
    plan = Plan(steps=[Step(kind='social', owner_text=message, topic='goodbye')])
    for person in 'abc':
        learned_social.observe(conn, lock, message, catalog, plan, person)
    assert learned_social.lookup(conn, lock, message, catalog) is None


def test_the_table_stays_small(client, monkeypatch):
    from app import learned_social
    from app.conversation_plan import Plan, Step
    monkeypatch.setattr(learned_social, 'MAX_PHRASES', 2)
    conn, lock, catalog = learned(client)
    for word in ('cheers', 'later', 'ciao'):
        learned_social.observe(conn, lock, word, catalog, Plan(steps=[Step(kind='social', owner_text=word, topic='goodbye')]), 'a')
    assert conn.execute('SELECT COUNT(*) FROM learned_social').fetchone()[0] == 2


def test_hermes_teaches_a_goodbye_then_it_is_answered_without_hermes(client, hermes, semantic):
    from app import learned_social
    from app.conversation_plan import Plan, Step
    from app.penny_conversation import FAREWELL
    fake = scripted(hermes, social_reply('see ya', 'goodbye'))
    events, _ = turn(client, 'see ya')
    assert fake.requests and FAREWELL in json.dumps(events)
    conn, lock, catalog = learned(client)
    for person in ('b', 'c'):
        learned_social.observe(conn, lock, 'see ya', catalog, Plan(steps=[Step(kind='social', owner_text='see ya', topic='goodbye')]), person)
    fake = scripted(hermes, social_reply('see ya', 'goodbye'))
    events, _ = turn(client, 'See ya!')
    assert not fake.requests and FAREWELL in json.dumps(events)


def test_nothing_is_learned_with_the_flag_off(client, hermes, monkeypatch):
    monkeypatch.delenv('CHAT_SEMANTIC_STATE', raising=False)
    scripted(hermes, social_reply('see ya', 'goodbye'))
    turn(client, 'see ya')
    conn, _, _ = learned(client)
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='learned_social'").fetchone() or \
        not conn.execute('SELECT COUNT(*) FROM learned_social').fetchone()[0]
