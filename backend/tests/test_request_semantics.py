import json
from pathlib import Path

import pytest

from app import db
from app.request_semantics import (Interpretation, RequestState, apply_delta, excluded_task_ids, excluded_tool_ids,
                                   inline_schema, owner_budget, start_turn, wanted_task_ids, MAX_FACTS)


@pytest.fixture(scope='module')
def catalog():
    return db.load_catalog_file(Path(__file__).resolve().parent.parent / 'data' / 'catalog.json')


@pytest.fixture(scope='module')
def ids(catalog):
    tasks = [card.id for card in catalog.problem_cards]
    tools = [tool.id for tool in catalog.tools if tool.status != 'rejected']
    return tasks, tools


def delta(*ops, **extra):
    return Interpretation.model_validate({'ops': list(ops), **extra})


def add(kind, evidence, scope='business', strength='hard', task_id=None, tool_ids=(), budget=None):
    return {'op': 'add', 'evidence': evidence, 'fact': {'kind': kind, 'strength': strength, 'scope': scope,
            'task_id': task_id, 'tool_ids': list(tool_ids), 'budget': budget, 'evidence': evidence}}


def usd(amount):
    return {'amount': amount, 'currency': 'USD', 'period': 'month', 'basis': 'total'}


def test_a_refusal_and_the_wanted_task_are_stored_separately(catalog, ids):
    reviews = next(task for task in ids[0] if 'review' in task)
    scheduling = next(task for task in ids[0] if 'schedul' in task)
    message = "I don't need help with reviews, just scheduling"
    state, outcome = apply_delta(RequestState(), delta(add('task_exclusion', "I don't need help with reviews", task_id=reviews),
                                                       add('deliverable', 'just scheduling', task_id=scheduling)),
                                 message, catalog, [])
    assert outcome.rejected == [] and outcome.conflicts == []
    assert excluded_task_ids(state) == {reviews}
    assert wanted_task_ids(state) == [scheduling]


def test_evidence_must_be_the_owners_current_words(catalog, ids):
    state, outcome = apply_delta(RequestState(), delta(add('task_exclusion', 'no bookkeeping', task_id=ids[0][0])),
                                 'I need help with scheduling', catalog, [])
    assert state.facts == [] and outcome.rejected == [(0, 'evidence_not_in_message')]


def test_unknown_ids_are_rejected(catalog, ids):
    message = 'not that one'
    _, outcome = apply_delta(RequestState(), delta(add('task_exclusion', message, task_id='made_up_task'),
                                                   add('product_exclusion', message, tool_ids=['made_up_tool'])),
                             message, catalog, [])
    assert [reason for _, reason in outcome.rejected] == ['unknown_task', 'unknown_tool']


def test_one_bad_op_does_not_drop_the_good_ones(catalog, ids):
    message = 'skip it, I need scheduling'
    state, outcome = apply_delta(RequestState(), delta(add('task_exclusion', 'invented words', task_id=ids[0][0]),
                                                       add('deliverable', 'I need scheduling', task_id=ids[0][1])),
                                 message, catalog, [])
    assert len(outcome.rejected) == 1 and wanted_task_ids(state) == [ids[0][1]]


def test_a_budget_correction_replaces_the_old_budget(catalog):
    state, _ = apply_delta(RequestState(), delta(add('budget', 'under $50 a month', budget=usd(50))),
                           'I can spend under $50 a month', catalog, [])
    state = start_turn(state, [])
    state, outcome = apply_delta(state, delta(add('budget', 'actually $80', budget=usd(80))),
                                 'actually $80 is fine', catalog, [])
    assert len(state.facts) == 1 and outcome.applied == ['f1']
    assert owner_budget(state).amount == 80


def test_the_narrowest_budget_wins(catalog):
    message = 'overall $200 a month, but for this one under $30'
    state, _ = apply_delta(RequestState(), delta(add('budget', 'overall $200 a month', budget=usd(200)),
                                                 add('budget', 'for this one under $30', scope='current_action', budget=usd(30))),
                           message, catalog, [])
    assert owner_budget(state).amount == 30
    assert owner_budget(start_turn(state, [])).amount == 200


def test_unmentioned_facts_survive_later_turns(catalog, ids):
    state, _ = apply_delta(RequestState(), delta(add('task_exclusion', 'not bookkeeping', task_id=ids[0][0])),
                           'not bookkeeping', catalog, [])
    for message in ('what about pricing?', 'thanks'):
        state, _ = apply_delta(start_turn(state, []), delta(), message, catalog, [])
    assert excluded_task_ids(state) == {ids[0][0]}


def test_a_new_task_does_not_inherit_a_task_scoped_exclusion(catalog, ids):
    tasks, tools = ids
    state, _ = apply_delta(RequestState(), delta(add('product_exclusion', 'not that tool', scope='active_task', tool_ids=[tools[0]])),
                           'not that tool', catalog, [tasks[0]])
    assert excluded_tool_ids(start_turn(state, [tasks[0]])) == {tools[0]}
    assert excluded_tool_ids(start_turn(state, [tasks[1]])) == set()


def test_quoted_or_third_party_words_are_not_owner_facts(catalog, ids):
    task = ids[0][0]
    for message, evidence in (('my friend said no bookkeeping tools', 'my friend said no bookkeeping tools'),
                              ('the review said "skip bookkeeping" lol', 'skip bookkeeping')):
        state, outcome = apply_delta(RequestState(), delta(add('task_exclusion', evidence, task_id=task)), message, catalog, [])
        assert state.facts == [] and outcome.rejected == [(0, 'third_party_or_hypothetical')]
        state, _ = apply_delta(RequestState(), delta(add('task_exclusion', evidence, scope='scenario', task_id=task)), message, catalog, [])
        assert len(state.facts) == 1 and excluded_task_ids(state) == set()


def test_a_clash_is_reported_not_resolved(catalog, ids):
    task = ids[0][0]
    state, _ = apply_delta(RequestState(), delta(add('task_exclusion', 'not bookkeeping', task_id=task)),
                           'not bookkeeping', catalog, [])
    after, outcome = apply_delta(start_turn(state, []), delta(add('deliverable', 'I need bookkeeping', task_id=task)),
                                 'I need bookkeeping', catalog, [])
    assert [c.fact_id for c in outcome.conflicts] == ['f1'] and excluded_task_ids(after) == {task}
    assert wanted_task_ids(after) == []


def test_withdrawing_then_asking_is_a_change_of_mind(catalog, ids):
    task = ids[0][0]
    state, _ = apply_delta(RequestState(), delta(add('task_exclusion', 'not bookkeeping', task_id=task)),
                           'not bookkeeping', catalog, [])
    message = 'actually I do need bookkeeping'
    state, outcome = apply_delta(start_turn(state, []),
                                 delta({'op': 'withdraw', 'target_id': 'f1', 'evidence': 'actually'},
                                       add('deliverable', 'I do need bookkeeping', task_id=task)),
                                 message, catalog, [])
    assert outcome.withdrawn == ['f1'] and excluded_task_ids(state) == set() and wanted_task_ids(state) == [task]


@pytest.mark.parametrize('op, reason', [
    ({'op': 'withdraw', 'target_id': 'f9', 'evidence': 'drop it'}, 'unknown_target'),
    ({'op': 'replace', 'target_id': 'f9', 'evidence': 'drop it',
      'fact': add('budget', 'drop it', budget=usd(5))['fact']}, 'unknown_target'),
    ({'op': 'add', 'evidence': 'drop it'}, 'missing_fact'),
])
def test_ops_without_a_valid_target_are_rejected(catalog, op, reason):
    _, outcome = apply_delta(RequestState(), delta(op), 'drop it', catalog, [])
    assert outcome.rejected == [(0, reason)]


def test_replace_cannot_change_what_kind_of_fact_it_is(catalog, ids):
    state, _ = apply_delta(RequestState(), delta(add('budget', 'under $50', budget=usd(50))), 'under $50', catalog, [])
    op = {'op': 'replace', 'target_id': 'f1', 'evidence': 'not bookkeeping',
          'fact': add('task_exclusion', 'not bookkeeping', task_id=ids[0][0])['fact']}
    _, outcome = apply_delta(state, delta(op), 'not bookkeeping', catalog, [])
    assert outcome.rejected == [(0, 'replace_changes_kind')]


def test_state_is_bounded(catalog):
    state = RequestState()
    for amount in range(MAX_FACTS + 1):
        text = f'rule {amount}'
        state, outcome = apply_delta(state, delta(add('approach_constraint', text)), text, catalog, [])
    assert len(state.facts) == MAX_FACTS and outcome.rejected == [(0, 'state_full')]


def test_budget_shape_must_match_kind(catalog):
    _, outcome = apply_delta(RequestState(), delta(add('approach_constraint', 'cheap', budget=usd(5)),
                                                   add('budget', 'cheap')), 'cheap', catalog, [])
    assert [reason for _, reason in outcome.rejected] == ['budget_shape', 'budget_shape']


def test_unresolved_needs_must_be_owner_words(catalog):
    _, outcome = apply_delta(RequestState(), delta(unresolved=['fix my van', 'teleport']), 'can you fix my van', catalog, [])
    assert outcome.unresolved == ['fix my van']


def test_state_round_trips_through_json(catalog, ids):
    state, _ = apply_delta(RequestState(), delta(add('budget', 'under $50', budget=usd(50))), 'under $50', catalog, [])
    assert RequestState.model_validate(json.loads(state.model_dump_json())) == state


def test_strict_schema_has_no_refs_and_lists_every_property():
    schema = inline_schema(Interpretation)
    text = json.dumps(schema)
    assert '$ref' not in text and '$defs' not in text

    def walk(value):
        if isinstance(value, dict):
            if value.get('type') == 'object':
                assert value['additionalProperties'] is False
                assert set(value['required']) == set(value['properties'])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)


def test_old_memory_and_snapshots_without_requirements_still_load():
    from app.chat import ChatMemory
    from app.chat_history import ChatSnapshot
    assert ChatMemory.model_validate({'trade': None}).requirements == RequestState()
    snapshot = ChatSnapshot.model_validate({'conversationId': 'c1', 'messages': [], 'memory': {'owned': []}})
    assert snapshot.memory.requirements.facts == []


def test_snapshot_keeps_requirements(catalog):
    from app.chat import ChatMemory
    from app.chat_history import ChatSnapshot
    state, _ = apply_delta(RequestState(), delta(add('budget', 'under $50', budget=usd(50))), 'under $50', catalog, [])
    snapshot = ChatSnapshot(conversationId='c1', memory=ChatMemory(requirements=state))
    assert ChatSnapshot.model_validate_json(snapshot.model_dump_json()).memory.requirements == state


def test_clean_memory_drops_facts_the_catalog_no_longer_knows(catalog, ids):
    from app.chat import ChatMemory, _clean_memory
    state, _ = apply_delta(RequestState(), delta(add('task_exclusion', 'not that', task_id=ids[0][0]),
                                                 add('budget', 'under $50', budget=usd(50))),
                           'not that, under $50', catalog, [])
    smaller = catalog.model_copy(deep=True)
    smaller.problem_cards = [card for card in smaller.problem_cards if card.id != ids[0][0]]
    cleaned = _clean_memory(smaller, ChatMemory(requirements=state)).requirements
    assert [fact.kind for fact in cleaned.facts] == ['budget'] and cleaned.next_id == 3


def test_forgetting_the_business_clears_requirements(catalog):
    from app.chat import ChatMemory
    from app.conversation_plan import forget_business
    state, _ = apply_delta(RequestState(), delta(add('budget', 'under $50', budget=usd(50))), 'under $50', catalog, [])
    memory = ChatMemory(requirements=state)
    forget_business(memory)
    assert memory.requirements == RequestState()


@pytest.mark.parametrize('marker, present', [('', False), ('\nrequest_semantics_v1', True)])
def test_gateway_adds_semantics_only_when_the_app_asks(marker, present):
    from app.model_gateway import _pinned
    system = 'penny_hermes_selection_v1\nconversation_state_v2\nconversation_plan_v3' + marker
    schema = _pinned({'messages': [{'role': 'system', 'content': system}]})['response_format']['json_schema']['schema']
    assert ('semantics' in schema['properties']) is present and ('semantics' in schema['required']) is present
    if present:
        assert '$ref' not in json.dumps(schema['properties']['semantics'])
