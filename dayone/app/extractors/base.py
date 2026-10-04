"""Extractor interface.  The rest of the system only knows this contract, so
the vision model can be swapped or disabled without touching anything else.

An extractor receives IMAGE BYTES ONLY (never the registry PDF, the CSV or
any ground truth) and returns a RawExtraction:

    {"page_type": "<one of schema.PAGE_TYPES>",
     "fields":  {field_key: [value, status, confidence]},
     "groups":  {group_name: {slot_id: {field_key: [value, status, confidence]}}}}

status is one of RAW_STATUSES; confidence is in [0, 1].
"""
from __future__ import annotations

RAW_STATUSES = ("KNOWN", "NOT_PROVIDED", "ILLEGIBLE", "UNKNOWN", "STRUCK")


class ExtractionError(Exception):
    """The model answered but the answer is unusable (counts as an attempt)."""


# Why the AI cannot be used right now.  Codes, not sentences: the chat turns
# them into a message in the midwife's language (see app/i18n.py, "ai_why.*").
WHY_OFFLINE = "offline"                # no connection (or the demo switch)
WHY_SWITCHED_OFF = "switched_off"      # AI extraction disabled
WHY_NO_KEY = "no_key"                  # no API key on this device
WHY_OVERLOADED = "overloaded"          # the provider answers 5xx
WHY_RATE_LIMITED = "rate_limited"      # the provider answers 429 (quota / rate limit)
WHY_KEY_REFUSED = "key_refused"        # the provider refuses the key (401 / 403)
WHY_MODEL_UNKNOWN = "model_unknown"    # the provider does not know the model id (404)
WHY_NETWORK = "network"                # the provider cannot be reached
WHY_CODES = (WHY_OFFLINE, WHY_SWITCHED_OFF, WHY_NO_KEY, WHY_OVERLOADED, WHY_RATE_LIMITED,
             WHY_KEY_REFUSED, WHY_MODEL_UNKNOWN, WHY_NETWORK)


class ExtractorUnavailable(Exception):
    """No network / no key / disabled / provider down.  NOT a failure of the
    page: it stays queued and nothing counts against its retry budget.

    `why` is one of WHY_CODES.  `provider` is True when the AI service itself
    was reached (or should have been) and did not do the work: the worker then
    waits before trying again and tells the midwife what is happening.
    """

    def __init__(self, message: str = "", why: str = "", provider: bool = False):
        super().__init__(message)
        self.why, self.provider = why, provider


class Extractor:
    name = "base"

    def available(self) -> bool:
        raise NotImplementedError

    def why_unavailable(self) -> str:
        """One of WHY_CODES when `available()` is False, else ""."""
        return ""

    def extract(self, image: bytes, mime: str = "image/jpeg", page_type_hint: str | None = None) -> dict:
        raise NotImplementedError


class DisabledExtractor(Extractor):
    """AI switched off: every record goes through manual entry."""
    name = "disabled"

    def available(self) -> bool:
        return False

    def why_unavailable(self) -> str:
        return WHY_SWITCHED_OFF

    def extract(self, image, mime="image/jpeg", page_type_hint=None):
        raise ExtractorUnavailable("AI extraction is disabled", WHY_SWITCHED_OFF)
