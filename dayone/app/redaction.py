"""Direct-identifier redaction.

Three layers, applied before anything reaches storage or logs:
1. Structural: the schema has no field for names, CIN, phone, address or
   examiner names, and any key the extractor returns that is not in the
   schema is dropped (see pipeline.py).
2. Pattern scrub of every free-text value (`scrub`).
3. A logging filter that scrubs every log line (`install_log_filter`).

Entries may be written in French, English or Arabic: digits in Arabic script
are converted before the patterns run (a phone number written ٠٦... must not
slip through), and the English and Arabic titles and address words are
handled with the same policy as the French ones (app/languages.py).

Limitation (documented in the README): a bare personal name written inside a
free-text clinical field cannot be recognised by patterns alone.
"""
from __future__ import annotations

import logging
import re

from . import languages

MASK = "[REDACTED]"

_PATTERNS = (
    # Moroccan national ID (CIN): 1-2 letters + 5-7 digits
    ("national_id", re.compile(r"\b[A-Za-z]{1,2}\s?\d{5,7}\b")),
    # phone numbers: +212 / 0 followed by 9 digits, any spacing
    ("phone", re.compile(r"(?:\+|00)\s?\d{2,3}(?:[\s.\-]?\d){8,10}\b|\b0\s?[5-8](?:[\s.\-]?\d){8}\b")),
    ("long_number", re.compile(r"\b\d{9,}\b")),
    # street-level address
    ("address", re.compile(
        r"(?:\b(?:n[°o]\s*)?\d{1,4}\s*,?\s*(?:bis\s+)?)?\b(?:rue|avenue|av\.|bd\.?|boulevard|lotissement|lot\.?|hay|"
        r"derb|quartier|r[ée]sidence|immeuble|imm\.?|douar|appt\.?|appartement)\s+[^,;\n]+(?:,\s*[^,;\n]+)?",
        re.IGNORECASE)),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
)
# A title followed by a capitalised name: keep the role, drop the name.
_TITLED_NAME = re.compile(
    r"\b(Dr|Docteur|Pr|Mme|Mlle|Mr|M\.|Inf|Infirmi[eè]re?|Sage[- ]femme|SF)\.?\s+"
    r"((?:(?:El|Al|Ben|Ait|Aït|Bou)\s+)?[A-ZÀ-Ý][\w'’\-]+(?:\s+[A-ZÀ-Ý][\w'’\-]+)?)")


def scrub(text: str, allow: tuple = ()) -> tuple[str, list[str]]:
    """Return (clean_text, kinds_of_identifier_removed).

    `allow` lists kinds that are legitimate content for the field being cleaned
    (schema.SCRUB_ALLOW): a health centre is often named after its neighbourhood
    ("CSC Hay Salam"), which is the facility's name, not a patient's address.
    """
    if not isinstance(text, str) or not text:
        return text, []
    found = []
    out = languages.western_digits(text)

    def titled(m):
        found.append("person_name")
        return f"{m.group(1)} {MASK}"                 # keep the role, drop the name

    for pat in (_TITLED_NAME, *languages.TITLED_NAME_PATTERNS):
        out = pat.sub(titled, out)
    for kind, pat in (*_PATTERNS, *(("address", p) for p in languages.ADDRESS_PATTERNS)):
        if kind not in allow and pat.search(out):
            found.append(kind)
            out = pat.sub(MASK, out)
    return out, found


def contains_identifier(text: str) -> bool:
    return bool(scrub(text)[1])


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:        # never let logging break the app
            return True
        record.msg, record.args = scrub(msg)[0], ()
        return True


def install_log_filter() -> None:
    f = RedactingFilter()
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)


def get_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in lg.filters):
        lg.addFilter(RedactingFilter())
    return lg
