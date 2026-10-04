"""Field schema and status model, derived from the eight page types of the
paper registry ("Fiche de surveillance de la grossesse et du post-partum").

This module is the single source of truth for: what each field is, which
statuses it may take, how a blank cell is classified, and which fields
structurally depend on others.  It contains NO ground-truth values.

Direct identifiers (woman's name, husband's name, CIN, phone, address, and
the names of examiners) deliberately have NO field here: the extractor is
never asked for them and there is nowhere to store them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from . import languages

# --------------------------------------------------------------------------
# Field status model (names exactly as in the challenge PDF)
# --------------------------------------------------------------------------
KNOWN = "KNOWN"                    # a value was read / entered
UNKNOWN = "UNKNOWN"                # the paper itself says it is unknown ("?", "inconnu")
NOT_PROVIDED = "NOT_PROVIDED"      # the cell is blank and it could have been filled
ILLEGIBLE = "ILLEGIBLE"            # something is written but cannot be read
NOT_APPLICABLE = "NOT_APPLICABLE"  # struck through, or ruled out by another field
NEEDS_REVIEW = "NEEDS_REVIEW"      # not final: the midwife must look at it

FIELD_STATUSES = (KNOWN, UNKNOWN, NOT_PROVIDED, ILLEGIBLE, NOT_APPLICABLE, NEEDS_REVIEW)
FINAL_STATUSES = (KNOWN, UNKNOWN, NOT_PROVIDED, ILLEGIBLE, NOT_APPLICABLE)
VALUE_STATUSES = (KNOWN, NEEDS_REVIEW)   # only these may carry a value

# Field kinds
TEXT, INT, NUM, DATE, BOOL, CHOICE, MULTI, BP = "text", "int", "number", "date", "bool", "choice", "multi", "bp"
# A single checkbox is stored and shown as "ticked" / "not ticked" - never as yes/no.
TICKED, NOT_TICKED = "ticked", "not ticked"


@dataclass(frozen=True)
class Field:
    key: str
    label: str                 # printed French label on the form
    kind: str = TEXT
    en: str = ""               # English display name
    options: tuple = ()        # printed option labels for CHOICE / MULTI
    unit: str = ""
    lo: float | None = None    # plausibility range (validation only; out of
    hi: float | None = None    # range => NEEDS_REVIEW, never "corrected")
    # layout hint used only by the ground-truth builder (eval/):
    #   ("kv", n)            n-th occurrence of `label`, value to its right
    #   ("cell", row, col)   table cell
    #   ("below", n)         value written under a column header
    #   ("box", n)           single checkbox, n-th occurrence of label
    #   ("opts", (n1, n2..)) occurrence index of each option label
    loc: tuple = ()
    # checkbox group: boxes printed together on the form.  If NO box of a group
    # is ticked, every box in it is NOT_PROVIDED (see apply_blank_rules).
    group: str = ""
    # always asked for confirmation, whatever the confidence
    always_confirm: bool = False

    @property
    def title(self) -> str:
        return self.en or self.key.replace("_", " ").capitalize()


@dataclass(frozen=True)
class RepeatGroup:
    """A repeating list (antenatal visits, previous deliveries)."""
    name: str
    slots: tuple            # (slot_id, printed column header, occurrence)
    fields: tuple           # Field, with loc = ("row", row_label, n)
    en: str = ""


@dataclass(frozen=True)
class Section:
    key: str                # page type
    title_fr: str
    en: str
    fields: tuple
    groups: tuple = ()

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)


def F(key, label, kind=TEXT, en="", options=(), unit="", lo=None, hi=None, loc=(), group="", confirm=False):
    return Field(key, label, kind, en, tuple(options), unit, lo, hi, tuple(loc), group, confirm)


# --------------------------------------------------------------------------
# Page 1 - cover
# --------------------------------------------------------------------------
COVER = Section("cover", "Fiche de surveillance (couverture)", "Cover", (
    F("fiche_number", "N° de la fiche :", en="Registry / linking code", confirm=True),
    F("region", "Région :", en="Region"),
    F("province", "Province :", en="Province"),
    F("facility_name", "Nom de l'établissement sanitaire :", en="Health facility"),
    F("facility_type", "Type de l'établissement sanitaire :", CHOICE, "Facility type",
      ("DR", "CSC", "CSU", "CSCA", "CSUA")),
    F("coverage_mode", "Mode de la couverture", CHOICE, "Coverage mode", ("Fixe", "Mobile")),
    F("high_risk_pregnancy", "Grossesse classée à risque :", BOOL,
      "Pregnancy recorded as high-risk on paper", loc=("box", 1), group="risk"),
    F("risk_types", "Si grossesse à risque, préciser le type de risque :", MULTI,
      "Risk types ticked on paper",
      ("Anémie", "H.T.A", "Diabète", "Cardiopathie", "Métrorragie", "Infection",
       "Pré-éclampsie", "Eclampsie"), group="risk"),
))

# --------------------------------------------------------------------------
# Page 2 - identification and history
# --------------------------------------------------------------------------
_FAM_ROWS = (("hypertension", "HTA"), ("diabetes", "Diabète"),
             ("hereditary_diseases", "Maladies héréditaires"),
             ("malformations", "Malformations"), ("allergies", "Allergie(s)"))
_ANOM_ROWS = (("abortion", "Avortement"), ("premature_delivery", "Accouchement prématuré"),
              ("fetal_death", "Mort fœtale in utéro"), ("other_anomaly", "Autres à préciser :"))
_ANOM_COLS = (("count", "Nombre", INT), ("date", "Date", DATE), ("place", "Lieu", TEXT),
              ("gestational_age_weeks", "Age gestationnel (SA)", NUM))

IDENTIFICATION = Section("identification", "Identification et antécédents",
                         "Identification and history", (
    F("age", "Age :", INT, "Age (years)", lo=10, hi=60, unit="years"),
    F("education_level", "Niveau d'instruction :", en="Education level"),
    F("profession", "Profession :", en="Occupation", loc=("kv", 1)),
    F("consanguinity", "Consanguinité", BOOL, "Consanguinity", loc=("box", 1), group="identity_boxes"),
    F("desired_pregnancy", "Grossesse désirée", BOOL, "Desired pregnancy", loc=("box", 1), group="identity_boxes"),
    *[F(f"family_{k}_{side}", lab, en=f"Family history - {k.replace('_', ' ')} ({who})",
        loc=("cell", lab, col))
      for k, lab in _FAM_ROWS
      for side, col, who in (("woman", "Famille de la femme", "woman's family"),
                             ("husband", "Mari/famille", "husband's family"))],
    F("history_medical", "Médicaux", en="Medical history", loc=("below", 1)),
    F("history_surgical", "Chirurgicaux", en="Surgical history", loc=("below", 1)),
    F("history_gynecological", "Gynécologiques", en="Gynecological history", loc=("below", 1)),
    *[F(f"{rk}_{ck}", rl, kind, f"{rk.replace('_', ' ').capitalize()} - {ck.replace('_', ' ')}",
        loc=("cell", rl, cl))
      for rk, rl in _ANOM_ROWS for ck, cl, kind in _ANOM_COLS],
    F("gravidity", "Gestation :", INT, "Gravidity", lo=0, hi=20),
    F("parity", "Parité :", INT, "Parity", lo=0, hi=20),
    F("living_children", "Nombre d'enfants vivants :", INT, "Living children", lo=0, hi=20),
    F("tetanus_doses", "VAT :", MULTI, "Tetanus vaccine doses ticked (VAT)", ("1", "2", "3", "4", "5"), group="vaccination"),
    F("rubella_vaccinated", "Vaccinée contre la rubéole", BOOL, "Vaccinated against rubella", loc=("box", 1), group="vaccination"),
    F("rubella_vaccine_date", "Le", DATE, "Rubella vaccination date", loc=("kv", 1)),
    F("hepatitis_b_vaccinated", "Vaccinée contre l'hépatite B", BOOL, "Vaccinated against hepatitis B", loc=("box", 1), group="vaccination"),
    F("hepatitis_b_vaccine_date", "Le", DATE, "Hepatitis B vaccination date", loc=("kv", 2)),
    F("cervical_smear", "Frottis cervical / IVA (moins de 3 ans) :", en="Cervical smear / VIA (< 3 years)"),
), groups=(
    RepeatGroup("previous_deliveries", tuple((str(i), f"Accouch. {i}", 1) for i in range(1, 6)), (
        F("date", "Date", DATE, "Date", loc=("row", "Date", 2)),
        F("mode", "Modalité d'extraction", en="Mode of delivery", loc=("row", "Modalité d'extraction", 1)),
        F("cesarean_indication", "Si césarienne : indication", en="Cesarean indication",
          loc=("row", "Si césarienne : indication", 1)),
        F("complication", "Complication (type)", en="Complication", loc=("row", "Complication (type)", 1)),
        F("newborn_weight_g", "Poids nouveau-né(s)", NUM, "Newborn weight (g)", unit="g", lo=300, hi=7000,
          loc=("row", "Poids nouveau-né(s)", 1)),
        F("newborn_complication", "Compl. nouveau-né (type)", en="Newborn complication",
          loc=("row", "Compl. nouveau-né (type)", 1)),
    ), en="Previous delivery"),
))

# --------------------------------------------------------------------------
# Page 3 - current pregnancy (longitudinal)
# --------------------------------------------------------------------------
VISIT_SLOTS = (
    ("t1_v1", "Visite 1", 1), ("t1_v2", "Visite 2", 1), ("t1_v3", "Visite 3", 1),
    ("t2_v1", "Visite 1", 2), ("t2_v2", "Visite 2", 2), ("t2_v3", "Visite 3", 2),
    ("m7", "7ème mois", 1), ("m8", "8ème mois", 1), ("m9", "9ème mois", 1),
)
SLOT_TITLES = {"t1_v1": "1st trimester, visit 1", "t1_v2": "1st trimester, visit 2",
               "t1_v3": "1st trimester, visit 3", "t2_v1": "2nd trimester, visit 1",
               "t2_v2": "2nd trimester, visit 2", "t2_v3": "2nd trimester, visit 3",
               "m7": "7th month", "m8": "8th month", "m9": "9th month"}


def _row(key, label, kind=TEXT, en="", **kw):
    return F(key, label, kind, en, loc=("row", label, 1), **kw)


PREGNANCY = Section("pregnancy", "Grossesse actuelle", "Current pregnancy", (
    F("lmp_date", "DDR :", DATE, "Last menstrual period (DDR)"),
    F("height_cm", "Taille :", NUM, "Height (cm)", unit="cm", lo=100, hi=220),
    F("blood_group", "Groupage :", CHOICE, "Blood group", ("A", "B", "O", "AB")),
    F("rhesus", "Rhésus", CHOICE, "Rhesus", ("Rh-", "Rh+")),
    F("expected_delivery_date", "DATE PRÉVUE D'ACCOUCHEMENT :", DATE, "Expected delivery date"),
    F("term_overrun_date", "DATE DE DÉPASSEMENT DE TERME :", DATE, "Post-term date"),
), groups=(
    RepeatGroup("visits", VISIT_SLOTS, (
        _row("appointment_date", "Rendez-vous", DATE, "Appointment date"),
        _row("attended_date", "Venue le", DATE, "Date attended"),
        _row("reminder_visit", "Visites de relance", en="Reminder visit"),
        _row("gestational_age_weeks", "Age probable", NUM, "Gestational age (weeks)", unit="SA", lo=1, hi=45),
        _row("weight_kg", "Poids (kg)", NUM, "Weight (kg)", unit="kg", lo=25, hi=200),
        _row("blood_pressure", "TA", BP, "Blood pressure"),
        _row("skeletal_anomalies", "Anomalies squelette", en="Skeletal anomalies"),
        _row("conjunctiva", "État des conjonctives", en="Conjunctiva"),
        _row("breast_exam", "Examen des seins", en="Breast exam"),
        _row("edema", "Œdèmes", en="Edema"),
        _row("fetal_movements", "Mouvements actifs", en="Fetal movements"),
        _row("fundal_height_cm", "HU (cm)", NUM, "Fundal height (cm)", unit="cm", lo=5, hi=50),
        _row("fetal_heart_rate", "BCF", NUM, "Fetal heart rate", unit="bpm", lo=60, hi=220),
        _row("speculum_exam", "Examen au spéculum", en="Speculum exam"),
        _row("cervix_state", "TV : état du col", en="Vaginal exam: cervix"),
        _row("presentation", "TV : présentation", en="Vaginal exam: presentation"),
        _row("pelvis", "TV : bassin", en="Vaginal exam: pelvis"),
        _row("glycosuria", "Glucosurie", en="Glycosuria"),
        _row("albuminuria", "Albuminurie", en="Albuminuria"),
        _row("rubella", "Rubéole", en="Rubella serology"),
        _row("toxoplasmosis", "Toxoplasmose", en="Toxoplasmosis serology"),
        _row("syphilis", "Syphilis (TPHA/VDRL)", en="Syphilis test (TPHA/VDRL)", confirm=True),
        _row("hbs_antigen", "Ag HBs", en="Hepatitis B surface antigen", confirm=True),
        _row("hiv", "Sérologie VIH", en="HIV serology", confirm=True),
        _row("hemoglobin_g_dl", "Hémoglobine", NUM, "Hemoglobin (g/dL)", unit="g/dL", lo=3, hi=20),
        _row("platelets", "Plaquettes", en="Platelets"),
        _row("glycemia_g_l", "Bilan glycémique", NUM, "Blood glucose (g/L)", unit="g/L", lo=0.2, hi=6),
        _row("rai", "RAI (si Rh négatif)", en="Irregular antibody screen (if Rh-)"),
        _row("iron", "Fer", en="Iron prescribed"),
    ), en="Antenatal visit"),
))

# --------------------------------------------------------------------------
# Page 4 - delivery
# --------------------------------------------------------------------------
DELIVERY = Section("delivery", "Déroulement de l'accouchement", "Delivery", (
    F("place_setting", "Lieu", CHOICE, "Place of delivery", ("En milieu surveillé", "A domicile"), group="place"),
    F("supervised_place", "En milieu surveillé", CHOICE, "Supervised setting",
      ("Maison d'accouchement", "Maternité", "Clinique privée"), group="place"),
    F("home_assisted_by_qualified_staff", "Assisté par un personnel qualifié", BOOL,
      "Home birth assisted by qualified staff", loc=("box", 1), group="place"),
    F("delivery_date", "Date de l'accouchement :", DATE, "Delivery date"),
    F("mode", "Mode de l'accouchement :", CHOICE, "Mode of delivery",
      ("Voie basse non instrumentale", "Voie basse instrumentale",
       "Césarienne : Programmée", "Urgence"), group="mode"),
    F("instrumental_details", "Voie basse instrumentale", MULTI, "Instrumental details",
      ("Forceps", "Ventouse", "Avec épisiotomie"), group="mode"),
    F("cesarean_indication", "Préciser l'indication :", en="Cesarean indication"),
    F("complications_present", "Présence de complications", BOOL, "Complications present", loc=("box", 1), group="complications"),
    F("complication_timing", "Présence de complications", MULTI, "Complication timing",
      ("Au moment de l'accouchement", "Suites de couches"), group="complications"),
    F("complication_types", "Type de complications :", MULTI, "Complication types",
      ("Pré-éclampsie", "Eclampsie", "Hémorragie", "Infection", "Autres"), group="complications"),
    F("newborn_status", "Etat du nouveau-né :", CHOICE, "Newborn status",
      ("Vivant", "Mort-né", "Décès < 24 heures")),
    F("newborn_sex", "Sexe :", en="Newborn sex"),
    F("birth_weight_g", "Poids à la naissance :", NUM, "Birth weight (g)", unit="g", lo=300, hi=7000),
    F("head_circumference_cm", "Périmètre crânien à la naissance :", NUM,
      "Head circumference at birth (cm)", unit="cm", lo=15, hi=50),
    F("abnormality", "Anomalie à préciser :", en="Abnormality"),
    F("gestational_age_at_birth_weeks", "Âge gestationnel :", NUM, "Gestational age at birth (weeks)",
      unit="SA", lo=15, hi=46),
))

# --------------------------------------------------------------------------
# Pages 5 & 7 - postpartum consultation, mother (early / late share a layout)
# --------------------------------------------------------------------------
def _pp_mother(key, title_fr, en, timing_opts):
    return Section(key, title_fr, en, (
        F("timing", "Moment de la consultation", CHOICE, "Timing of consultation", timing_opts),
        F("consultation_date", "Date de la consultation :", DATE, "Consultation date"),
        F("temperature_c", "T°", NUM, "Temperature (°C)", unit="°C", lo=33, hi=43),
        F("blood_pressure", "TA", BP, "Blood pressure"),
        F("pulse", "Pouls", NUM, "Pulse", unit="bpm", lo=30, hi=220),
        F("weight_kg", "Poids", NUM, "Weight (kg)", unit="kg", lo=25, hi=200),
        F("conjunctiva", "Etat des conjonctives :", CHOICE, "Conjunctiva", ("Normales", "Décolorées")),
        F("uterine_globe_present", "Présence du globe utérin", BOOL, "Uterine globe present", loc=("box", 1)),
        F("lochia", "Etat des lochies :", MULTI, "Lochia",
          ("Fade", "fétide", "claires", "sanglantes", "Jaunâtres")),
        F("perineum", "Etat du périnée :", MULTI, "Perineum", ("Normal", "Épisiotomie", "Déchirure"),
          loc=("opts", (1, 1, 1)), group="perineum"),
        F("perineum_repaired", "Réparée", BOOL, "Perineum repaired", loc=("box", 1), group="perineum"),
        F("sphincters", "Etat des sphincters (anal et urétral) :", CHOICE, "Sphincters",
          ("Normal", "Anormal"), loc=("opts", (2, 1))),
        F("cesarean", "Césarienne :", BOOL, "Cesarean", loc=("box", 1)),
        F("scar_state", "Etat de la cicatrice :", en="Scar condition"),
        F("breasts", "Etat des seins :", MULTI, "Breasts", ("Normal", "lymphangite", "mastite et abcès"),
          loc=("opts", (3, 1, 1))),
        F("calves", "Etat des mollets :", MULTI, "Calves",
          ("Normal", "Rouges", "Chauds", "Douloureux à la dorsiflexion"), loc=("opts", (4, 1, 1, 1))),
        F("complications_present", "Présence de complication :", BOOL, "Complications present", loc=("box", 1), group="complications"),
        F("complications", "Présence de complication :", MULTI, "Complications",
          ("Hémorragie", "Infection", "Eclampsie", "Phlébite", "Complications mammaires", "Anémie", "Autres"), group="complications"),
        F("medication_intake", "Notion de prise de médicaments :", BOOL, "Reports taking medication",
          loc=("box", 1)),
        F("medication_details", "Notion de prise de médicaments :", en="Medication taken (as written)"),
        F("treatment_prescribed", "Traitement prescrit :", MULTI, "Treatment prescribed", ("Fer", "Vitamine A")),
        F("next_appointment_date", "Prochain rendez-vous le", DATE, "Next appointment"),
        F("fp_wants_method", "Désire utiliser une méthode", BOOL, "Family planning: wants a method",
          loc=("box", 1), group="family_planning"),
        F("fp_method", "Si oui, laquelle :", MULTI, "Family planning method", ("pilule", "DIU"), group="family_planning"),
        F("fp_prescription_done", "Prescription faite", BOOL, "Family planning: prescription given",
          loc=("box", 1), group="family_planning"),
        F("fp_referred", "Référée :", BOOL, "Family planning: referred", loc=("box", 1), group="family_planning"),
        F("fp_reason_no_method", "Si la mère ne désire pas une méthode contraceptive : Pourquoi ?",
          en="Reason for not wanting contraception", loc=("under", 1)),
    ))


PP_EARLY_MOTHER = _pp_mother(
    "pp_early_mother", "Consultation du post-partum précoce - mère", "Early postpartum - mother",
    ("Entre le 7ème et 8ème jour après l'accouchement", "Après le 8ème jour de l'accouchement"))
PP_LATE_MOTHER = _pp_mother(
    "pp_late_mother", "Consultation du post-partum tardif - mère", "Late postpartum - mother",
    ("Entre le 40ème et 50ème jour après l'accouchement", "Après le 50ème jour de l'accouchement"))


# --------------------------------------------------------------------------
# Pages 6 & 8 - postpartum consultation, newborn
# --------------------------------------------------------------------------
def _pp_newborn(key, title_fr, en):
    return Section(key, title_fr, en, (
        F("consultation_date", "Date de la consultation :", DATE, "Consultation date"),
        F("age", "Age", en="Newborn age"),
        F("temperature_c", "Température", NUM, "Temperature (°C)", unit="°C", lo=30, hi=43),
        F("weight_g", "Poids", NUM, "Weight (g)", unit="g", lo=300, hi=15000),
        F("length_cm", "Taille", NUM, "Length (cm)", unit="cm", lo=20, hi=90),
        F("head_circumference_cm", "Périmètre crânien", NUM, "Head circumference (cm)", unit="cm", lo=15, hi=60),
        F("premature", "Nouveau-né prématuré", BOOL, "Premature newborn", loc=("box", 1), group="newborn_flags"),
        F("hypotrophic", "Nouveau-né hypotrophe", BOOL, "Low birth weight (hypotrophe)", loc=("box", 1), group="newborn_flags"),
        F("feeding", "Allaitement :", CHOICE, "Feeding", ("exclusivement au sein", "Artificiel", "mixte")),
        F("danger_signs", "Signes d'une affection grave :", MULTI, "Danger signs ticked on paper",
          ("Convulsions", "Refus de téter", "Hématémèses", "Mélaenas", "Diarrhée", "Ictère",
           "Tirage sous costal", "Toux", "Rythme respiratoire anormal", "Fièvre", "Hypothermie")),
        F("danger_signs_other", "Autres à préciser :", en="Other danger signs", loc=("kv", 1)),
        F("trauma_malformations", "Contusions, lésions traumatiques et malformations :", MULTI,
          "Bruises, trauma, malformations",
          ("Bosse sérosanguine ou céphalohématome", "Luxation congénitale de la hanche",
           "Diminution ou absence de la mobilité d'un membre")),
        F("trauma_other", "Autres à préciser :", en="Other trauma / malformation", loc=("kv", 2)),
        F("breastfeeding_assessment", "Evaluation de l'allaitement maternel :", CHOICE,
          "Breastfeeding assessment", ("Normal", "A problèmes")),
        F("vaccines_today", "Vaccins administrés ce jour :", MULTI, "Vaccines given today", ("BCG", "HB"), group="vaccination"),
        F("vitamin_d", "Supplémentation en vitamine D", BOOL, "Vitamin D supplementation", loc=("box", 1), group="vaccination"),
        F("complications", "Présence de complications et de malformation :", MULTI, "Complications",
          ("Ictère", "Infection", "Conjonctivite", "Traumatisme", "Malformation", "Autres"),
          loc=("opts", (2, 1, 1, 1, 1, 1))),
        F("decision", "Décision prise :", en="Decision recorded"),
        F("treatment", "Traitement prescrit :", en="Treatment recorded"),
        F("transfer", "Transfert", BOOL, "Referral / transfer", loc=("box", 1)),
        F("referral_facility", "Préciser l'établissement de référence :", en="Referral facility"),
        F("followup_date", "Revenir pour une visite de suivi nécessaire le", DATE, "Follow-up visit date"),
    ))


PP_EARLY_NEWBORN = _pp_newborn("pp_early_newborn", "Consultation du post-partum précoce - nouveau-né",
                               "Early postpartum - newborn")
PP_LATE_NEWBORN = _pp_newborn("pp_late_newborn", "Consultation du post-partum tardif - nouveau-né",
                              "Late postpartum - newborn")

SECTIONS = (COVER, IDENTIFICATION, PREGNANCY, DELIVERY,
            PP_EARLY_MOTHER, PP_EARLY_NEWBORN, PP_LATE_MOTHER, PP_LATE_NEWBORN)
SECTION_BY_KEY = {s.key: s for s in SECTIONS}
PAGE_TYPES = tuple(s.key for s in SECTIONS)

# --------------------------------------------------------------------------
# Dependency rules: (section, field, parent, predicate on parent value)
# A BLANK field becomes NOT_APPLICABLE when the predicate is true for a parent
# whose status is KNOWN.  A written value is never overridden.
# --------------------------------------------------------------------------
def _is_false(v): return v == NOT_TICKED
def _is_true(v): return v == TICKED
def _not_in(*opts): return lambda v: v is not None and v not in opts
def _lacks(*opts): return lambda v: isinstance(v, list) and not any(o in v for o in opts)


DEPENDENCIES = (
    ("identification", "rubella_vaccine_date", "rubella_vaccinated", _is_false),
    ("identification", "hepatitis_b_vaccine_date", "hepatitis_b_vaccinated", _is_false),
    ("delivery", "supervised_place", "place_setting", _not_in("En milieu surveillé")),
    ("delivery", "home_assisted_by_qualified_staff", "place_setting", _not_in("A domicile")),
    ("delivery", "instrumental_details", "mode", _not_in("Voie basse instrumentale")),
    ("delivery", "cesarean_indication", "mode", _not_in("Césarienne : Programmée", "Urgence")),
    *[(s, "perineum_repaired", "perineum", _lacks("Épisiotomie", "Déchirure"))
      for s in ("pp_early_mother", "pp_late_mother")],
    *[(s, "fp_method", "fp_wants_method", _is_false) for s in ("pp_early_mother", "pp_late_mother")],
    *[(s, "fp_prescription_done", "fp_wants_method", _is_false) for s in ("pp_early_mother", "pp_late_mother")],
    *[(s, "fp_reason_no_method", "fp_wants_method", _is_true) for s in ("pp_early_mother", "pp_late_mother")],
)
# group-level rules: (section, group, field, parent field in same item | section field, predicate, scope)
GROUP_DEPENDENCIES = (
    ("identification", "previous_deliveries", "cesarean_indication", "mode",
     lambda v: isinstance(v, str) and "sar" not in v.lower(), "item"),      # not a c(é)sarienne
    ("pregnancy", "visits", "rai", "rhesus", lambda v: v == "Rh+", "section"),
)

STRIKE_MARKS = {"—", "–", "-", "--", "/", "//", "\\", "x", "X", "∅", "ø"}
# Fields whose value is made of digits (see the optional stricter threshold of pipeline.finalize).
NUMERIC_KINDS = (INT, NUM, DATE, BP)

# Kinds of scrubbing (app/redaction.py) that must NOT be applied to a field, because what looks
# like an identifier is the field's legitimate content.  Measured on the dataset: the facility
# names "CSC Hay Salam" and "CSUA Hay Riad" were masked as addresses, and the midwife could not
# type them back either.
SCRUB_ALLOW = {"facility_name": ("address",)}

# "Unknown" as written on paper, in French, plus English and Arabic (app/languages.py).
UNKNOWN_MARKS = {"?", "??", "inconnu", "inconnue", "ne sait pas", "nsp", "unknown"} | languages.UNKNOWN_MARKS


# --------------------------------------------------------------------------
# Field value container and helpers
# --------------------------------------------------------------------------
def fv(value=None, status=NOT_PROVIDED, confidence=None, source="none", note=""):
    """One field value.  `value` is only kept for KNOWN / NEEDS_REVIEW."""
    if status not in FIELD_STATUSES:
        raise ValueError(f"invalid field status {status!r}")
    if status not in VALUE_STATUSES:
        value = None
    if status == KNOWN and (value is None or value == ""):
        raise ValueError("KNOWN requires a value")
    return {"value": value, "status": status, "confidence": confidence, "source": source, "note": note}


def empty_section(section_key: str, status=NEEDS_REVIEW, source="none") -> dict:
    """A section with every field unset (used for manual entry)."""
    sec = SECTION_BY_KEY[section_key]
    return {"fields": {f.key: fv(None, status, None, source) for f in sec.fields},
            "groups": {g.name: [] for g in sec.groups}}


def iter_fields(section_key: str, data: dict) -> Iterator[tuple[str, Field, dict]]:
    """Yield (path, Field, field_value) for every field present in a section.
    Paths look like `age` or `visits[t1_v2].weight_kg`."""
    sec = SECTION_BY_KEY[section_key]
    for f in sec.fields:
        if f.key in data.get("fields", {}):
            yield f.key, f, data["fields"][f.key]
    for g in sec.groups:
        gf = {f.key: f for f in g.fields}
        for item in data.get("groups", {}).get(g.name, []):
            for k, v in item["fields"].items():
                if k in gf:
                    yield f"{g.name}[{item['slot']}].{k}", gf[k], v


def get_path(data: dict, path: str) -> dict:
    if "[" in path:
        g, rest = path.split("[", 1)
        slot, key = rest.split("].", 1)
        for item in data["groups"][g]:
            if item["slot"] == slot:
                return item["fields"][key]
        raise KeyError(path)
    return data["fields"][path]


def set_path(data: dict, path: str, value: dict) -> None:
    if "[" in path:
        g, rest = path.split("[", 1)
        slot, key = rest.split("].", 1)
        for item in data["groups"][g]:
            if item["slot"] == slot:
                item["fields"][key] = value
                return
        raise KeyError(path)
    data["fields"][path] = value


def path_title(section_key: str, path: str) -> str:
    sec = SECTION_BY_KEY[section_key]
    if "[" in path:
        g, rest = path.split("[", 1)
        slot, key = rest.split("].", 1)
        grp = next(x for x in sec.groups if x.name == g)
        f = next(x for x in grp.fields if x.key == key)
        slot_name = SLOT_TITLES.get(slot, f"#{slot}")
        return f"{grp.en} {slot_name} - {f.title}"
    return sec.field(path).title


def _is_blank(t: dict) -> bool:
    """Blank on paper, nothing ticked, or not yet answered during manual entry."""
    return (t["status"] == NOT_PROVIDED or (t["status"] == KNOWN and t["value"] in ([], NOT_TICKED))
            or (t["status"] == NEEDS_REVIEW and t["value"] is None and t.get("source") == "none"))


def checkbox_groups(section_key: str) -> dict:
    """{group name: [Field, ...]} for the checkbox fields of a section."""
    out: dict = {}
    for f in SECTION_BY_KEY[section_key].fields:
        if f.kind in (BOOL, CHOICE, MULTI):
            out.setdefault(f.group or f.key, []).append(f)
    return out


def apply_group_rule(section_key: str, fields: dict) -> None:
    """An unticked box only means "not ticked" when the midwife used its group.

    If no box at all is ticked in a checkbox group, every box of the group is
    NOT_PROVIDED.  The demotion is reversible (flag `unticked`) so that a tick
    entered later in the same group restores "not ticked" for its siblings.
    A box whose state is itself uncertain (NEEDS_REVIEW) is left alone.
    """
    for members in checkbox_groups(section_key).values():
        members = [f for f in members if f.key in fields]
        used = any(fields[f.key]["status"] in VALUE_STATUSES and fields[f.key]["value"] not in (None, NOT_TICKED, [])
                   for f in members)
        for f in members:
            if f.kind == CHOICE:
                continue
            v, empty = fields[f.key], (NOT_TICKED if f.kind == BOOL else [])
            if not used and v["status"] == KNOWN and v["value"] == empty:
                fields[f.key] = {**v, "value": None, "status": NOT_PROVIDED, "unticked": True,
                                 "note": "no box ticked in this group"}
            elif used and v["status"] == NOT_PROVIDED and v.get("unticked"):
                fields[f.key] = {**v, "value": empty, "status": KNOWN, "unticked": False, "note": ""}


def apply_blank_rules(section_key: str, data: dict) -> dict:
    """Apply the NOT_PROVIDED / NOT_APPLICABLE rule (see README, 'Blank cells').

    0. Checkbox groups: see apply_group_rule.
    1. A cell struck by the midwife (dash, slash) is NOT_APPLICABLE - handled
       upstream, where the written mark is seen.
    2. A BLANK cell is NOT_APPLICABLE when a dependency rule rules it out.
    3. Every other blank cell is NOT_PROVIDED.
    4. A repeating-group column that is entirely blank produces no item.
    Only blank (NOT_PROVIDED) fields are touched; written values never are.
    """
    # rule 4: drop fully blank repeating items
    for g, items in list(data.get("groups", {}).items()):
        data["groups"][g] = [it for it in items
                             if any(v["status"] not in (NOT_PROVIDED,) for v in it["fields"].values())]
    fields = data.get("fields", {})
    apply_group_rule(section_key, fields)
    for sk, fk, parent, pred in DEPENDENCIES:
        if sk != section_key or fk not in fields or parent not in fields:
            continue
        p, t = fields[parent], fields[fk]
        if _is_blank(t) and p["status"] == KNOWN and pred(p["value"]):
            fields[fk] = {**t, **fv(None, NOT_APPLICABLE, t.get("confidence"), t.get("source", "none"),
                                    f"not applicable given {parent}"), "unticked": False}
    for sk, g, fk, parent, pred, scope in GROUP_DEPENDENCIES:
        if sk != section_key:
            continue
        for item in data.get("groups", {}).get(g, []):
            p = item["fields"].get(parent) if scope == "item" else fields.get(parent)
            t = item["fields"].get(fk)
            if p and t and _is_blank(t) and p["status"] == KNOWN and pred(p["value"]):
                item["fields"][fk] = fv(None, NOT_APPLICABLE, t.get("confidence"), t.get("source", "none"),
                                        f"not applicable given {parent}")
    return data


def count_fields() -> dict:
    out = {}
    for s in SECTIONS:
        out[s.key] = {"fields": len(s.fields),
                      "groups": {g.name: (len(g.slots), len(g.fields)) for g in s.groups}}
    return out
