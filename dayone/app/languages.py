"""Everything the app knows about entries written in English or Arabic.

The registry form is PRINTED in French, so field labels and checkbox options
stay French.  What a midwife WRITES in a cell may be French, English or
Arabic.  This module holds the language knowledge in one place (it imports nothing
from the app, so every other module may use it); it is used by

* normalize.canon   - to type values (numbers, dates) whatever the script;
* redaction.scrub   - so identifiers written in Arabic or English are scrubbed too;
* schema.UNKNOWN_MARKS - the words that mean "unknown";
* the prompt in extractors/gemini.py mirrors the same rules in plain language.

(The LANGUAGE OF THE INTERFACE, French or English, is a different matter: see i18n.py.)

Nothing here translates or "repairs" a value: it only maps different ways of
writing the SAME thing (a digit, a month name) onto one form.  Checkboxes are
not affected: a box is ticked or not ticked whatever the language.

STATUS: unit-tested only.  The provided dataset has no Arabic or English
entries, so reading accuracy on those languages has not been measured.
"""
from __future__ import annotations

import re
import unicodedata

# --- digits and punctuation -------------------------------------------------
# Arabic-Indic digits, their Persian/Urdu variants, and Arabic punctuation,
# mapped to what the French-oriented parsers already understand.
_TRANSLATE = {ord(a): str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}
_TRANSLATE.update({ord(a): str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_TRANSLATE.update({
    ord("٫"): ".",      # Arabic decimal separator
    ord("٬"): None,     # Arabic thousands separator
    ord("،"): ",",      # Arabic comma
    ord("؛"): ";",      # Arabic semicolon
    ord("؟"): "?",      # Arabic question mark
    ord("٪"): "%",      # Arabic percent sign
    ord("ـ"): None,     # tatweel (letter-stretching stroke, carries no meaning)
})
# Invisible direction marks that models sometimes emit around right-to-left text.
_TRANSLATE.update({c: None for c in (0x200E, 0x200F, 0x061C, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                                     0x2066, 0x2067, 0x2068, 0x2069)})


_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")


def has_arabic(text) -> bool:
    """True when the text contains Arabic letters or Arabic-script digits."""
    return isinstance(text, str) and bool(_ARABIC_SCRIPT.search(text))


def western_digits(text):
    """'٣٦٢٦ غ' -> '3626 غ'.  Non-strings are returned unchanged."""
    return text.translate(_TRANSLATE) if isinstance(text, str) else text


def _key(text) -> str:
    """Accent-, case- and space-insensitive key (same idea as normalize.fold)."""
    t = unicodedata.normalize("NFKD", western_digits(str(text)))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return "".join(c for c in t if c.isalnum())


# --- "unknown" written on paper ----------------------------------------------
# Added to schema.UNKNOWN_MARKS (which already has the French ones).
UNKNOWN_MARKS = {
    "not known", "don't know", "dont know", "do not know",
    "غير معروف", "غير معروفة", "مجهول", "لا أعرف", "لا تعرف", "لا يعرف", "غير محدد",
}

# --- month names ---------------------------------------------------------------
# French, English, and Arabic (both the Maghreb and the Middle-East names).
_MONTHS = {
    1: "janvier janv jan january يناير",
    2: "fevrier fevr fev february feb فبراير",
    3: "mars mar march مارس",
    4: "avril avr april apr أبريل",
    5: "mai may ماي مايو",
    6: "juin june jun يونيو",
    7: "juillet juil july jul يوليوز يوليو",
    8: "aout august aug غشت أغسطس",
    9: "septembre sept sep september شتنبر سبتمبر",
    10: "octobre oct october أكتوبر",
    11: "novembre nov november نونبر نوفمبر",
    12: "decembre dec december دجنبر ديسمبر",
}
MONTH_BY_NAME = {_key(name): number for number, names in _MONTHS.items() for name in names.split()}

_LETTERS = r"[^\W\d_]+"
_DAY_MONTH_YEAR = re.compile(rf"^\s*(\d{{1,2}})(?:er|st|nd|rd|th)?[\s/.\-]+({_LETTERS})\.?[\s/.\-,]+(\d{{2,4}})\s*$")
_MONTH_DAY_YEAR = re.compile(rf"^\s*({_LETTERS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(\d{{4}})\s*$")


def numeric_date(text: str):
    """'12 mai 2022', 'May 12, 2022', '١٢ ماي ٢٠٢٢' -> '12/5/2022'.

    Returns None when the text is not a date with a month NAME; all-numeric
    dates are left to normalize.canon.  An all-numeric date is always read as
    day/month/year (the registry's convention): 03/04/2022 is never guessed
    to be March 4th.
    """
    s = western_digits(text)
    m = _DAY_MONTH_YEAR.match(s)
    if m:
        day, name, year = m.groups()
    else:
        m = _MONTH_DAY_YEAR.match(s)
        if not m:
            return None
        name, day, year = m.groups()
    month = MONTH_BY_NAME.get(_key(name))
    return f"{int(day)}/{month}/{year}" if month else None


# --- identifiers written in English or Arabic -----------------------------------
# redaction.py already handles the French forms.  Same policy here: keep the
# role, drop the name; drop street-level addresses.  A bare personal name
# inside free text still cannot be recognised by patterns (see README).
_AR = "\u0621-\u064A"
TITLED_NAME_PATTERNS = (
    re.compile(r"\b(Mrs|Ms|Miss|Doctor|Nurse|Midwife)\.?\s+([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?)"),
    re.compile(rf"(الدكتورة|الدكتور|د\.|السيدة|السيد|الآنسة|الممرضة|الممرض|القابلة)\s+([{_AR}]+(?:\s+[{_AR}]+)?)"),
)
ADDRESS_PATTERNS = (
    re.compile(r"(?:\b\d{1,4}\s*,?\s+)?(?:[A-Z][\w'’\-]+\s+){1,3}(?:Street|St\.|Road|Rd\.|Avenue|Ave\.)(?=\W|$)"),
    re.compile(r"(?:شارع|زنقة|دوار|إقامة|عمارة)\s+[^,;\n]+"),
)
