"""Record lifecycle: states, failure states and the allowed transitions.

Happy path (names exactly as in the challenge PDF):
  CAPTURED -> PENDING_AI -> AI_PROCESSED -> NEEDS_REVIEW -> VALIDATED
           -> PATIENT_MATCHED -> REGISTERED -> SYNCED
The PDF describes four failure states without naming them; these names are ours:
  PROCESSING_FAILED, SYNC_FAILED, DUPLICATE_SUSPECTED, MANUAL_REVIEW_REQUIRED
"""
from __future__ import annotations

CAPTURED = "CAPTURED"
PENDING_AI = "PENDING_AI"
AI_PROCESSED = "AI_PROCESSED"
NEEDS_REVIEW = "NEEDS_REVIEW"
VALIDATED = "VALIDATED"
PATIENT_MATCHED = "PATIENT_MATCHED"
REGISTERED = "REGISTERED"
SYNCED = "SYNCED"
PROCESSING_FAILED = "PROCESSING_FAILED"
SYNC_FAILED = "SYNC_FAILED"
DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"

HAPPY_PATH = (CAPTURED, PENDING_AI, AI_PROCESSED, NEEDS_REVIEW, VALIDATED,
              PATIENT_MATCHED, REGISTERED, SYNCED)
FAILURE_STATES = (PROCESSING_FAILED, SYNC_FAILED, DUPLICATE_SUSPECTED, MANUAL_REVIEW_REQUIRED)
STATES = HAPPY_PATH + FAILURE_STATES

TRANSITIONS: dict[str, tuple[str, ...]] = {
    CAPTURED: (PENDING_AI,),
    # NEEDS_REVIEW from PENDING_AI = the midwife starts manual entry while AI is unavailable
    PENDING_AI: (AI_PROCESSED, PROCESSING_FAILED, NEEDS_REVIEW),
    # VALIDATED directly only when nothing is uncertain AND the midwife confirms
    AI_PROCESSED: (NEEDS_REVIEW, VALIDATED, DUPLICATE_SUSPECTED),
    # CAPTURED = retake a photo; PENDING_AI = let the AI finish a record started manually
    NEEDS_REVIEW: (VALIDATED, CAPTURED, PENDING_AI),
    # NEEDS_REVIEW = the midwife reopens the record; MANUAL_REVIEW_REQUIRED = "I'm not sure" at matching
    VALIDATED: (PATIENT_MATCHED, MANUAL_REVIEW_REQUIRED, NEEDS_REVIEW),
    PATIENT_MATCHED: (REGISTERED,),
    REGISTERED: (SYNCED, SYNC_FAILED),
    SYNCED: (),
    PROCESSING_FAILED: (PENDING_AI, MANUAL_REVIEW_REQUIRED),
    SYNC_FAILED: (REGISTERED,),
    DUPLICATE_SUSPECTED: (NEEDS_REVIEW,),
    # VALIDATED = an unresolved match decision is taken up again
    MANUAL_REVIEW_REQUIRED: (NEEDS_REVIEW, PENDING_AI, CAPTURED, VALIDATED),
}


class InvalidTransition(Exception):
    pass


def can_transition(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, ())


def check_transition(src: str, dst: str) -> None:
    if src not in TRANSITIONS or dst not in STATES:
        raise InvalidTransition(f"unknown state {src!r} -> {dst!r}")
    if not can_transition(src, dst):
        raise InvalidTransition(f"{src} -> {dst} is not allowed")
