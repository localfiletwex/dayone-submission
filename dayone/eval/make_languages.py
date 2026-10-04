"""Synthetic ENGLISH and ARABIC versions of the dataset pages, with reference values.

The provided dataset has French entries only, yet the brief scores extraction
on "French, Arabic, English".  This script makes the missing material from
the same ten synthetic patients:

    python -m eval.make_languages            # writes work/languages/{en,ar}/page-NN.png
                                             # and    work/ground_truth/gt-{en,ar}.json

For every page of the registry PDF it removes the handwritten entries that
belong to a schema field and writes them again in the other language, at the
same place, in a handwriting-style font.  Words are translated with the table
below (every handwritten word of the dataset is in it); numbers, dates and
blood pressures keep their value.  In the Arabic set, patients 6 to 10 also
have their digits written in Arabic script (٠-٩); patients 1 to 5 keep Western
digits, as is usual in Morocco.

What this material IS: the same forms, layouts and values, in two more
languages and scripts.  What it is NOT: real handwriting.  The Arabic is set
in handwriting-style typefaces (ruq'ah and informal styles), far more regular
than a midwife's hand, and the translations are mine.  A score on these pages
says whether the pipeline copes with the languages and scripts at all, not
how it will do on real Arabic handwriting.

Nothing in data/ is modified.  The reference values are produced by the same
code as the French ones (eval/build_ground_truth.py), then the handwritten
cells are replaced by what this script wrote on the page.

Fonts (SIL Open Font License, from github.com/google/fonts) are downloaded to
work/fonts/ on first use.
"""
from __future__ import annotations

import copy
import io
import json
import random
import re
import sys
import urllib.request

import pymupdf
from PIL import Image, ImageDraw, ImageFont, features

from app import schema as S
from app.languages import western_digits
from app.normalize import canon
from eval import build_ground_truth as B
from eval.paths import GT_PATH, REGISTRY_PDF, WORK

LANGS = ("en", "ar")
OUT_DIR = WORK / "languages"
FONT_DIR = WORK / "fonts"
DPI = 200                                   # the dataset's page images are 1654 x 2339 = A4 at 200 dpi
INK = (20, 33, 140)                         # colour of the handwriting in the dataset
ARABIC_DIGIT_PATIENTS = range(6, 11)        # in the Arabic set, these patients' numbers use ٠-٩

_GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONTS = {   # (file name, URL, size relative to the original handwriting)
    "en": [("Caveat.ttf", _GF + "caveat/Caveat%5Bwght%5D.ttf", 1.0),
           ("Gaegu-Regular.ttf", _GF + "gaegu/Gaegu-Regular.ttf", 0.95),
           ("NanumPenScript-Regular.ttf", _GF + "nanumpenscript/NanumPenScript-Regular.ttf", 1.0),
           ("ReenieBeanie.ttf", _GF + "reeniebeanie/ReenieBeanie.ttf", 1.0),
           ("ShadowsIntoLight.ttf", _GF + "shadowsintolight/ShadowsIntoLight.ttf", 0.9)],
    "ar": [("ArefRuqaa-Regular.ttf", _GF + "arefruqaa/ArefRuqaa-Regular.ttf", 0.9),
           ("Katibeh-Regular.ttf", _GF + "katibeh/Katibeh-Regular.ttf", 1.0),
           ("PlaypenSansArabic.ttf", _GF + "playpensansarabic/PlaypenSansArabic%5Bwght%5D.ttf", 0.7),
           ("Marhey.ttf", _GF + "marhey/Marhey%5Bwght%5D.ttf", 0.7)],
}

# French entry as written in the dataset -> (English, Arabic).
VOCAB = {
    "RAS": ("NAD", "لا شيء"), "Neg": ("Neg", "سلبي"), "Pos +": ("Pos +", "إيجابي +"),
    "Non": ("No", "لا"), "Oui": ("Yes", "نعم"),
    "Normaux": ("Normal", "طبيعية"), "Normales": ("Normal", "طبيعية"), "Normal": ("Normal", "طبيعي"),
    "Néant": ("None", "لا شيء"), "Aucun": ("None", "لا يوجد"), "Aucune": ("None", "لا يوجد"),
    "Poursuivre l'allaitement exclusif": ("Continue exclusive breastfeeding", "مواصلة الرضاعة الطبيعية الحصرية"),
    "Voie basse": ("Vaginal delivery", "ولادة طبيعية"),
    "Immune": ("Immune", "محصنة"), "Non immune": ("Not immune", "غير محصنة"),
    "Fermé": ("Closed", "مغلق"), "F": ("F", "أنثى"), "M": ("M", "ذكر"),
    "Céphalique": ("Cephalic", "رأسي"), "Cycles réguliers": ("Regular cycles", "دورات منتظمة"),
    "Propre": ("Clean", "نظيف"), "Propre, sèche": ("Clean, dry", "نظيف، جاف"), "Pâles": ("Pale", "شاحبة"),
    "Non fait": ("Not done", "غير منجز"),
    "Fer 2 cp/j": ("Iron 2 tab/day", "حديد 2 قرص/يوم"),
    "Vit. D 400 UI/j": ("Vit. D 400 IU/day", "فيتامين د 400 وحدة/يوم"),
    "Souhaite en discuter avec son mari": ("Wants to discuss with her husband", "ترغب في مناقشة الأمر مع زوجها"),
    "Agricultrice": ("Farmer", "فلاحة"), "Femme au foyer": ("Housewife", "ربة بيت"), "Étudiante": ("Student", "طالبة"),
    "Couturière": ("Seamstress", "خياطة"), "Commerçante": ("Shopkeeper", "تاجرة"), "Employée": ("Employee", "موظفة"),
    "Oncle": ("Uncle", "العم"), "Mère": ("Mother", "الأم"), "Père": ("Father", "الأب"),
    "Collège": ("Middle school", "إعدادي"), "Lycée": ("High school", "ثانوي"), "Supérieur": ("Higher education", "جامعي"),
    "Maternité": ("Maternity unit", "دار الولادة"),
    "Souffrance fœtale": ("Fetal distress", "معاناة جنينية"), "Asthme léger": ("Mild asthma", "ربو خفيف"),
    "Utérus cicatriciel": ("Scarred uterus", "رحم ندبي"), "Appendicectomie": ("Appendectomy", "استئصال الزائدة"),
    "Césarienne": ("Caesarean", "قيصرية"), "Pré-éclampsie sévère": ("Severe pre-eclampsia", "تسمم حمل شديد"),
    # places and facilities: proper nouns, unchanged in English, written in Arabic in the Arabic set
    "Rabat-Salé-Kénitra": (None, "الرباط-سلا-القنيطرة"), "Kénitra": (None, "القنيطرة"), "Khémisset": (None, "الخميسات"),
    "Fès-Meknès": (None, "فاس-مكناس"), "Meknès": (None, "مكناس"), "Marrakech-Safi": (None, "مراكش-آسفي"),
    "Al Haouz": (None, "الحوز"), "Béni Mellal-Khénifra": (None, "بني ملال-خنيفرة"), "Azilal": (None, "أزيلال"),
    "Souss-Massa": (None, "سوس-ماسة"), "Taroudant": (None, "تارودانت"),
    "Tanger-Tétouan-AlHoceïma": (None, "طنجة-تطوان-الحسيمة"), "Chefchaouen": (None, "شفشاون"),
    "Oriental": (None, "الشرق"), "Berkane": (None, "بركان"), "Casablanca-Settat": (None, "الدار البيضاء-سطات"),
    "Settat": (None, "سطات"), "Drâa-Tafilalet": (None, "درعة-تافيلالت"), "Errachidia": (None, "الرشيدية"),
    "CSCA Al Wifaq": (None, "المركز الصحي الوفاق"), "CSC Hay Salam": (None, "المركز الصحي حي السلام"),
    "DR Tahannaout Sud": (None, "المستوصف القروي تحناوت الجنوب"), "CSCA Ait Mhamed": (None, "المركز الصحي آيت محمد"),
    "CSC Al Amal": (None, "المركز الصحي الأمل"), "DR Bni Ahmed": (None, "المستوصف القروي بني أحمد"),
    "CSU Ennahda": (None, "المركز الصحي النهضة"), "CSCA Lahraouiyine": (None, "المركز الصحي الهراويين"),
    "CSC Ksar Jdid": (None, "المركز الصحي القصر الجديد"), "CSUA Hay Riad": (None, "المركز الصحي حي الرياض"),
}
_DAYS = re.compile(r"^(\d+)\s*jours$")
_CODE = re.compile(r"^[0-9A-Za-z+./ -]*\d[0-9A-Za-z+./ -]*$|^[A-Z]{2,5}$")     # "2026-823-001", "247k", "SFA"
_ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def _key(text: str) -> str:
    return "".join(text.split()).lower()


_BY_KEY = {_key(k): k for k in VOCAB}


def french_entry(raw: str) -> str | None:
    """The vocabulary entry behind a raw string of the dataset.  The dataset's own
    rendering sometimes drops a space or a glyph ("Voiebasse", "N\\ufffdant")."""
    key = _key(raw)
    if key in _BY_KEY:
        return _BY_KEY[key]
    if B.WILDCARD in raw:                                   # a glyph missing from the dataset's font
        pattern = re.compile("".join("." if c == B.WILDCARD else re.escape(c) for c in key))
        hits = [v for k, v in _BY_KEY.items() if pattern.fullmatch(k)]
        if len(set(hits)) == 1:
            return hits[0]
    return None


def translate(raw: str, kind: str, lang: str, arabic_digits: bool) -> tuple[str, bool]:
    """(text to write, translated?).  Values are never changed, only how they are written."""
    def digits(s):
        return s.translate(_ARABIC_DIGITS) if lang == "ar" and arabic_digits else s

    if kind != S.TEXT:                                      # numbers, dates, blood pressure
        return digits(raw), True
    m = _DAYS.match(raw.replace(" ", "") if " " not in raw.strip() else raw.strip())
    if m or _DAYS.match(re.sub(r"(\d)(jours)", r"\1 \2", raw)):
        n = int((m or _DAYS.match(re.sub(r"(\d)(jours)", r"\1 \2", raw))).group(1))
        return (f"{n} days", True) if lang == "en" else (f"{digits(str(n))} {'أيام' if 3 <= n <= 10 else 'يوما'}", True)
    entry = french_entry(raw)
    if entry is not None:
        en, ar = VOCAB[entry]
        return (en or entry, True) if lang == "en" else (digits(ar) if arabic_digits else ar, True)
    if _CODE.match(raw):                                    # registry codes and abbreviations stay as they are
        return raw, True
    return raw, False


# ----------------------------------------------------------------------------
# Reference values WITH the position of each handwritten entry
# ----------------------------------------------------------------------------
def build_with_positions():
    """Run the ordinary ground-truth builder, remembering which handwritten
    characters of the PDF make up each field."""
    last = {"chars": []}
    text_in, text_field = B.Page.text_in, B._text_field

    def text_in_spy(self, x0, x1, y0, y1):
        last["chars"] = [c for c in self.chars if x0 <= (c["x0"] + c["x1"]) / 2 < x1 and y0 <= c["cy"] < y1]
        return text_in(self, x0, x1, y0, y1)

    def text_field_spy(f, raw, font):
        res = text_field(f, raw, font)
        if res and res.get("mark") == "handwritten":
            res["_chars"] = [{k: c[k] for k in ("x0", "y0", "x1", "y1", "size", "line")} for c in last["chars"]]
        return res

    B.Page.text_in, B._text_field = text_in_spy, text_field_spy
    try:
        return B.build()[0]
    finally:
        B.Page.text_in, B._text_field = text_in, text_field


def _cells(page: dict):
    """(field, cell) for every field of a ground-truth page, tables included."""
    sec = S.SECTION_BY_KEY[page["page_type"]]
    for key, cell in page["fields"].items():
        yield sec.field(key), cell
    for g in sec.groups:
        by_key = {f.key: f for f in g.fields}
        for item in page.get("groups", {}).get(g.name, []):
            for key, cell in item["fields"].items():
                yield by_key[key], cell


# ----------------------------------------------------------------------------
# Drawing
# ----------------------------------------------------------------------------
def fonts(lang: str) -> list[tuple[str, float]]:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for name, url, scale in FONTS[lang]:
        path = FONT_DIR / name
        if not path.exists():
            print(f"  downloading font {name}", flush=True)
            try:
                path.write_bytes(urllib.request.urlopen(url, timeout=60).read())
            except OSError as e:
                sys.exit(f"Could not download {url} ({type(e).__name__}). Copy the font to {path} and run again.")
        out.append((str(path), scale))
    return out


def blank_page(doc, n: int, chars: list[dict]) -> Image.Image:
    """The page as an image, without the handwritten entries listed in `chars`
    (the printed form, the checkboxes and every other mark stay)."""
    page = doc[n - 1]
    for c in chars:                                         # a small box inside each character: only that glyph goes
        w, h = c["x1"] - c["x0"], c["y1"] - c["y0"]
        page.add_redact_annot(pymupdf.Rect(c["x0"] + 0.3 * w, c["y0"] + 0.3 * h, c["x1"] - 0.3 * w, c["y1"] - 0.3 * h))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    pix = page.get_pixmap(dpi=DPI)
    return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def _wrap(text: str, n_lines: int) -> list[str]:
    """Split a text into at most n_lines lines of similar length, at spaces."""
    words = text.split(" ")
    if n_lines <= 1 or len(words) < 2:
        return [text]
    target, lines, cur = len(text) / n_lines, [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > target * 1.15 and len(lines) < n_lines - 1:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    return lines + [cur]


def draw_entry(draw: ImageDraw.ImageDraw, text: str, chars: list[dict], font_path: str, scale: float,
               lang: str, rnd: random.Random) -> None:
    """Write `text` where the original entry was: same start, same baseline, same
    number of lines, shrunk if it would run far past the original's width."""
    k = DPI / 72.0
    line_ids = sorted({c["line"] for c in chars})
    size_pt = sorted(c["size"] for c in chars)[len(chars) // 2]
    for line_no, line in zip(line_ids, _wrap(text, len(line_ids))):
        cs = [c for c in chars if c["line"] == line_no]
        x0, x1 = min(c["x0"] for c in cs), max(c["x1"] for c in cs)
        y0, y1 = min(c["y0"] for c in cs), max(c["y1"] for c in cs)
        baseline = (y0 + 0.77 * (y1 - y0) + rnd.uniform(-0.5, 0.5)) * k
        room = ((x1 - x0) * 1.3 + 10) * k
        size = size_pt * k * scale * rnd.uniform(0.96, 1.04)
        # right-to-left only when there are Arabic LETTERS: "١٦٤ cm" (digits and a Latin unit) reads left to right
        kw = {"direction": "rtl", "language": "ar"} if re.search(r"[\u0621-\u064A]", line) else {}
        while True:
            font = ImageFont.truetype(font_path, max(8, round(size)), layout_engine=ImageFont.Layout.RAQM)
            if draw.textlength(line, font=font, **kw) <= room or size <= size_pt * k * scale * 0.6:
                break
            size *= 0.94
        draw.text(((x0 + rnd.uniform(-0.6, 0.6)) * k, baseline), line, font=font, fill=INK, anchor="ls", **kw)


# ----------------------------------------------------------------------------
def make(lang: str, base: dict, doc_path=REGISTRY_PDF) -> tuple[dict, list[str]]:
    """Write the images of one language and return (its reference values, entries left in French)."""
    out_dir = OUT_DIR / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    faces, gt, untranslated = fonts(lang), {}, []
    for key in sorted(base, key=int):
        page = copy.deepcopy(base[key])
        n, patient = page["page"], page["patient"]
        font_path, scale = faces[(patient - 1) % len(faces)]         # one "hand" per patient, as in the dataset
        rnd = random.Random(f"{lang}-{n}")
        todo = []
        for f, cell in _cells(page):
            chars = cell.pop("_chars", None)
            if cell.get("mark") != "handwritten" or not chars:
                continue
            text, ok = translate(str(cell["raw"]), f.kind, lang, patient in ARABIC_DIGIT_PATIENTS)
            if not ok:
                untranslated.append(f"p{n}:{cell['raw']}")
            value, typed = canon(f, text)
            cell.update(raw=text, value=value, typed=typed, wildcard=False, font=font_path.split("/")[-1], language=lang)
            todo.append((text, chars))
        doc = pymupdf.open(doc_path)
        img = blank_page(doc, n, [c for _, chars in todo for c in chars])
        draw = ImageDraw.Draw(img)
        for text, chars in todo:
            draw_entry(draw, text, chars, font_path, scale, lang, rnd)
        img.quantize(64, dither=Image.Dither.NONE).save(out_dir / f"page-{n:02d}.png", optimize=True)
        gt[key] = page
    return gt, untranslated


def main():
    if not features.check("raqm"):
        sys.exit("This Pillow build cannot shape Arabic text (no 'raqm' support). "
                 "Use the ready-made pages in dayone-languages.zip instead.")
    base = build_with_positions()
    for lang in LANGS:
        gt, untranslated = make(lang, base)
        path = GT_PATH.with_name(f"gt-{lang}.json")
        path.write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
        cells = [c for p in gt.values() for _, c in _cells(p) if c.get("language") == lang]
        print(f"{lang}: {len(gt)} pages in {OUT_DIR / lang}, {len(cells)} handwritten entries rewritten, "
              f"{sum(not c['typed'] for c in cells)} that cannot be typed, {len(untranslated)} left in French "
              f"{sorted(set(untranslated))[:6]}")
        print(f"    reference values: {path}")
    assert western_digits("٣") == "3"


if __name__ == "__main__":
    main()
