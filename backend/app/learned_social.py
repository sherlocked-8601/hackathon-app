"""Small talk that Hermes has already judged, remembered so the next one is answered without a model call.

Hermes itself keeps nothing between conversations. This table keeps its verdicts instead.
A phrase is only reused after several different people got the same verdict, and the reply
always comes from our own templates, so a learned phrase can never carry model-written text.
"""
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from .request_semantics import can_skip_hermes

logger = logging.getLogger(__name__)

MIN_PEOPLE = 3
MAX_PHRASES = 200
TOPICS = ('greeting', 'thanks', 'wellbeing', 'goodbye')
_SHAPE = re.compile(r"[a-z']+(?: [a-z']+){0,3}")
_REQUEST_WORDS = re.compile(r"\b(?:help|need|want|find|show|compare|recommend|tools?|apps?|software|price|prices|cost|"
                            r"save|remove|forget|remember|favou?rites?|business|account|delete|cancel|which|why|"
                            r"where|who|can|could|should|would|will)\b")
_TABLE = ('CREATE TABLE IF NOT EXISTS learned_social (phrase TEXT PRIMARY KEY, topic TEXT NOT NULL, '
          'people TEXT NOT NULL, seen INTEGER NOT NULL, last_seen TEXT NOT NULL)')


def normalize(message):
    text = ' '.join(message.casefold().replace('’', "'").split()).strip(' .!?')
    return text if _SHAPE.fullmatch(text) else None


def eligible(message, catalog):
    """Why: only a few plain words with no request in them can be learned. Anything with a limiter,
    a number, a question word or a catalog name stays with Hermes every time."""
    from .conversation_plan import mentioned_tools
    from .tasks import resolve_task
    text = normalize(message)
    if not text or _REQUEST_WORDS.search(text) or not can_skip_hermes(text, social=True):
        return None
    if mentioned_tools(catalog, text) or resolve_task(catalog, text).status == 'matched':
        return None
    return text


def _person(subject):
    return hashlib.sha256(subject.encode()).hexdigest()[:16]


def lookup(conn, lock, message, catalog):
    """The learned topic for this message, or None. A storage problem means Hermes decides."""
    text = conn is not None and eligible(message, catalog)
    if not text:
        return None
    try:
        with lock:
            conn.execute(_TABLE)
            row = conn.execute('SELECT topic, people FROM learned_social WHERE phrase=?', (text,)).fetchone()
    except sqlite3.Error:
        logger.warning('learned_social_unavailable')
        return None
    if not row or row[0] not in TOPICS or len(json.loads(row[1])) < MIN_PEOPLE:
        return None
    return row[0]


def observe(conn, lock, message, catalog, plan, subject):
    """Record what Hermes decided. A purely social one-step plan counts toward learning;
    any other verdict for the same phrase removes what was learned."""
    text = conn is not None and eligible(message, catalog)
    if not text:
        return
    steps = plan.steps if plan else []
    topic = steps[0].topic if len(steps) == 1 and steps[0].kind == 'social' else None
    now = datetime.now(timezone.utc).isoformat()
    try:
        with lock, conn:
            conn.execute(_TABLE)
            if topic not in TOPICS:
                conn.execute('DELETE FROM learned_social WHERE phrase=?', (text,))
                return
            row = conn.execute('SELECT topic, people FROM learned_social WHERE phrase=?', (text,)).fetchone()
            people = json.loads(row[1]) if row and row[0] == topic else []
            if not row and conn.execute('SELECT COUNT(*) FROM learned_social').fetchone()[0] >= MAX_PHRASES:
                return
            person = _person(subject)
            if person not in people:
                people = (people + [person])[-MIN_PEOPLE:]
            conn.execute('INSERT INTO learned_social VALUES(?,?,?,1,?) ON CONFLICT(phrase) DO UPDATE SET '
                         'topic=excluded.topic, people=excluded.people, seen=seen+1, last_seen=excluded.last_seen',
                         (text, topic, json.dumps(people), now))
    except sqlite3.Error:
        logger.warning('learned_social_unavailable')
