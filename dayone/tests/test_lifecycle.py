import itertools

import pytest

from app import lifecycle as L


def test_state_names_match_the_specification():
    assert L.HAPPY_PATH == ("CAPTURED", "PENDING_AI", "AI_PROCESSED", "NEEDS_REVIEW", "VALIDATED",
                            "PATIENT_MATCHED", "REGISTERED", "SYNCED")
    assert set(L.FAILURE_STATES) == {"PROCESSING_FAILED", "SYNC_FAILED", "DUPLICATE_SUSPECTED",
                                     "MANUAL_REVIEW_REQUIRED"}
    assert set(L.TRANSITIONS) == set(L.STATES)


def test_happy_path_is_allowed():
    for a, b in zip(L.HAPPY_PATH, L.HAPPY_PATH[1:]):
        L.check_transition(a, b)


def test_everything_not_listed_is_refused():
    for a, b in itertools.product(L.STATES, L.STATES):
        if b in L.TRANSITIONS[a]:
            continue
        with pytest.raises(L.InvalidTransition):
            L.check_transition(a, b)


@pytest.mark.parametrize("a,b", [("CAPTURED", "VALIDATED"), ("PENDING_AI", "SYNCED"), ("NEEDS_REVIEW", "REGISTERED"),
                                 ("VALIDATED", "REGISTERED"), ("VALIDATED", "SYNCED"), ("SYNCED", "CAPTURED"),
                                 ("AI_PROCESSED", "PATIENT_MATCHED"), ("CAPTURED", "CAPTURED")])
def test_dangerous_shortcuts_are_refused(a, b):
    assert not L.can_transition(a, b)


def test_synced_is_terminal_and_failures_have_an_exit():
    assert L.TRANSITIONS[L.SYNCED] == ()
    for s in L.FAILURE_STATES:
        assert L.TRANSITIONS[s], f"{s} would be a dead end"


def test_every_state_is_reachable_from_captured():
    seen, todo = {L.CAPTURED}, [L.CAPTURED]
    while todo:
        for n in L.TRANSITIONS[todo.pop()]:
            if n not in seen:
                seen.add(n)
                todo.append(n)
    assert seen == set(L.STATES)


def test_store_persists_transition_and_event_atomically(rig):
    rid = rig.store.create_record(rig.mw["id"])
    rig.store.transition(rid, L.PENDING_AI, reason="t")
    assert rig.state(rid) == L.PENDING_AI
    ev = [e for e in rig.store.events(rid) if e["kind"] == "state_change"]
    assert (ev[-1]["from_state"], ev[-1]["to_state"]) == (L.CAPTURED, L.PENDING_AI)


def test_store_refuses_invalid_transition_and_keeps_state(rig):
    rid = rig.store.create_record(rig.mw["id"])
    n = len(rig.store.events(rid))
    with pytest.raises(L.InvalidTransition):
        rig.store.transition(rid, L.SYNCED)
    assert rig.state(rid) == L.CAPTURED and len(rig.store.events(rid)) == n


def test_events_are_append_only(rig):
    rid = rig.store.create_record(rig.mw["id"])
    for sql in ("UPDATE events SET kind='x'", "DELETE FROM events"):
        with pytest.raises(Exception):
            rig.store.db.execute(sql)
    assert rig.store.events(rid)
