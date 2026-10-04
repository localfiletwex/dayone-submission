# DayOne - a midwife, a phone and an AI

Offline-first, WhatsApp-style agent that turns photos of a paper maternal registry
("Fiche de surveillance de la grossesse et du post-partum") into a structured record with a
status and a confidence per field, walks the midwife through review, and links each woman's
registries into one profile.

Synthetic hackathon data only. Out of scope and not built: risk prediction, triage colours,
clinical decision support, diagnosis, treatment recommendations.

## Quick start for reviewers

Needs Python 3.11 or later, on Linux, macOS or Windows. Developed and tested on Linux (Ubuntu
under WSL); the start, check and evaluation scripts are plain Python, so no `bash` is needed,
but the team has not run them on macOS or on Windows outside WSL.

```bash
python3 -m venv .venv
source .venv/bin/activate              # Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/check.py                # tests + start-up check, about 30 seconds, no key needed
python scripts/run_demo.py --fake      # the whole flow with INVENTED values, no key needed
```

Open http://127.0.0.1:8000, choose `midwife1`, type any PIN (the first login sets it), and send a
page image with the camera button or by dropping it on the chat. Test pages are in
`data/data/Paper Registry/`. `DEMO.md` is the four-minute demo script; stop with Ctrl+C.

To read real pages, give a Gemini API key (from Google AI Studio):

```bash
python scripts/run_demo.py --key YOUR_KEY
```

To check the numbers in this README **without a key**: the model's answers from our runs are
saved in `work/extraction_cache/`, so these commands make no model call. They need the
organisers' dataset in `./data` (so that `./data/data/Paper Registry/` exists); the submission
zip made by `scripts/pack_submission.py` already contains it.

```bash
python -m eval.evaluate --split test --clean-only    # French, test patients: 97.3% of filled fields
python -m eval.evaluate --split test --language ar   # the synthetic Arabic pages (also: en)
python -m eval.replay                                # what the app stores without asking, per language
```

The full evaluation from scratch needs a key: `python scripts/run_eval.py --key YOUR_KEY --clean-only`.
The `bash scripts/*.sh` commands used further down are thin wrappers around the same Python scripts.

If a start fails with "address already in use", an earlier copy is still running: stop it
(Linux/macOS: `pkill -f "uvicorn app"`). A forgotten PIN cannot be recovered: delete `device_data/`.

Where to look: `CRITERIA.md` maps each scoring criterion to code, evidence and known gaps;
`CHANGES.md` lists what changed and how it was tested; limitations are at the end of this file.

## Status at a glance

| Area | State |
|---|---|
| Schema, status model, blank-cell rule | Built, unit-tested |
| Encrypted storage, lifecycle, queue, sync | Built, tested (including connectivity cuts) |
| Chat flow (confirm / edit / retake / follow-ups / manual entry / multi-page / re-digitisation) | Built, tested with a scripted fake extractor and driven once in a real browser |
| Patient linking, redaction, role-based image access | Built, tested |
| Evaluation tooling (ground truth, split, degradation, scoring, calibration) | Built, scoring logic tested |
| Extraction accuracy, French, clean images | **Measured** on the test patients with `gemini-3.5-flash-lite` (see "Results") |
| Extraction accuracy, degraded images and real photos | **Not measured** (the run was `--clean-only`; the 5 photos have no reference values) |
| Extraction accuracy, English and Arabic entries | **Measured on synthetic pages** (`eval/make_languages.py`), test patients: 97.2% and 97.1% of filled fields. Arabic-script digits are the weak point: about 2% of stored values are wrong without having been asked (French 0.3%) unless the optional threshold for pages with Arabic entries is set (see "English and Arabic test pages") |
| Follow-up threshold | 0.50, chosen on the dev patients with the default prompt (v1) |
| Bonus: image-quality check on the device | Built, tested on every image of the dataset |
| Bonus: French / English interface | Built, tested (whole flow in both languages, in a browser) |
| Bonus: anonymised dashboard | Built, tested |
| Bonus: Arabic / multilingual entries | Typed and scrubbed like French ones (digits, dates, "unknown", identifiers); reading accuracy not measured. Telling the model about them is optional and **off**, because it lowered the accuracy on French (see "Results") |
| Bonus: real WhatsApp sandbox | Built, tested against a **simulated** WhatsApp service only; never run against the real one |

### Results (run by the team on 3 October 2026; files in `work/results/`)

Model `gemini-3.5-flash-lite`, thinking level `low`, prompt v1, clean images, French only.

| | Dev patients (48 pages) | Test patients (32 pages) |
|---|---|---|
| Filled fields read correctly | 98.3% (n=1274) | 97.3% (n=956) |
| Blank fields left blank | 99.1% (n=670) | 99.4% (n=507) |
| Values invented on blank cells | 0 | 0 |
| Page type recognised | 48 / 48 | 32 / 32 |
| At the threshold chosen on dev (0.50): filled fields asked | 3.4% | 4.3% |
| At that threshold: stored values that are wrong and were not asked | 0.2% | 0.3% (4 of 1416) |

The weakest page is identification and history (86.7% of filled fields on the test patients).

**Where the errors are.** Of the 26 filled fields read wrongly on the test patients, 21 are cells
of the previous-deliveries table on 2 pages where the model returned no column at all (dev: 15 of
22, on 1 page). A column that is not returned has no field to be unsure about. The agent
therefore asks, for every table, how many columns it read and whether another one is filled on
paper, and a registry cannot be confirmed before that question is answered. The evaluation counts
these cells as asked. Had the agent not asked (as before 4 October), the share of wrong values
stored without asking at threshold 0.50 would have been 0.9% on dev and 1.7% on test, not
0.2% and 0.3%. Making the model return those columns is the largest remaining gain in accuracy;
it needs a prompt change and a new evaluation run, and was not attempted.

**The language rules cost accuracy on French, so they are off by default.** Prompt v2 adds a
paragraph telling the model that entries may be in French, Arabic or English. Run on the dev
patients on 4 October 2026 (same model, same images, one run):

| Dev patients (48 pages) | Prompt v1 (default) | Prompt v2 (`DAYONE_PROMPT_LANGUAGES=1`) |
|---|---|---|
| Filled fields read correctly | 98.3% | 90.0% |
| Blank fields left blank | 99.1% | 95.8% |
| Values invented on blank cells | 0 | 25 (a visit placed in a column that is empty on paper) |
| Filled cells returned as blank or illegible | 16 | 115 |
| Stored values that are wrong and were not asked, at threshold 0.50 | 0.2% | 2.5% (no threshold reaches the 2% target) |

The loss is on the two pages with tables (identification 92.5% -> 80.0%, pregnancy 100% -> 85.4%);
the other six pages are unchanged. One run of each prompt cannot separate the effect of the
paragraph from day-to-day variation of the model, and it was not investigated further. v2 was not
run on the test patients. The model is therefore not told about Arabic or English by default;
what it returns in those languages is still typed and scrubbed by `app/languages.py`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Python 3.11+
pip install -r requirements.txt
# put the organisers' dataset in ./data so that "./data/data/Paper Registry/" exists
# (or point DAYONE_DATA at it). It is only ever read.
python -m pytest tests -q
bash scripts/check.sh          # the same tests, plus a start-up check and (with a key) one live call
```

Run the prototype:

```bash
GEMINI_API_KEY=... bash scripts/run_demo.sh        # real extraction
DAYONE_EXTRACTOR=fake bash scripts/run_demo.sh     # no key: canned INVENTED values, flow only
```

Open http://127.0.0.1:8000. Accounts: `midwife1`, `midwife2`, `supervisor1`, `data1`. The first
login sets the device PIN. `DEMO.md` is the demo script. The dashboard of anonymised aggregates
is at http://127.0.0.1:8001/dashboard. `WHATSAPP.md` explains the WhatsApp sandbox.

Run the evaluation (one command, your own key, results cached and resumable):

```bash
GEMINI_API_KEY=... bash scripts/run_eval.sh               # clean + synthetic degraded images
GEMINI_API_KEY=... bash scripts/run_eval.sh --clean-only  # clean images only
```

The script starts with a one-call smoke test of the key, the model ID and the thinking level,
and stops there if any is wrong. A full run is about 330 API calls (160 images x 2 calls, plus
5 photos); `--clean-only` halves that. `GEMINI_MIN_INTERVAL` spaces calls for low rate limits.

**Model.** The model ID is set in one place, `GEMINI_MODEL` in `app/config.py`; the
environment variable of the same name overrides it. The default is `gemini-3.5-flash-lite`,
the model the evaluation above was run with. (`gemini-3.8-flash` was the first choice; on
3 October 2026 it answered "overloaded" for hours, so the evaluation and the threshold were
done with the lite model. The app warns in its status box when it runs with a model other
than the one the threshold was calibrated with.) The thinking level is `GEMINI_THINKING_LEVEL`
(default `low`). Every results file records both, and the cache is kept per model, thinking
level and prompt version. The smoke test checks the key, the model ID and the thinking level
with one live call.

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | - | key for the hosted vision model |
| `DAYONE_EXTRACTOR` | `gemini` | `gemini`, `fake` or `disabled` |
| `DAYONE_DEVICE_DIR` | `./device_data` | encrypted device storage |
| `DAYONE_SYNC_URL` | `http://127.0.0.1:8001` | simulated central server |
| `DAYONE_THRESHOLD` | - | overrides the follow-up threshold |
| `DAYONE_THRESHOLD_ARABIC` | - | stricter threshold for the numbers of pages with Arabic entries (otherwise `threshold_arabic` in the threshold file, otherwise none) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | model ID |
| `GEMINI_THINKING_LEVEL` | `low` | thinking level; `none` sends no thinking configuration |
| `DAYONE_LANG` | `en` | interface language of an account that has not chosen one (`en` or `fr`) |
| `DAYONE_PROMPT_LANGUAGES` | `0` | `1` adds the French / Arabic / English rules to the prompt (prompt v2, lower accuracy on French) |
| `DAYONE_DASHBOARD_MIN_CELL` | `5` | smallest group the dashboard shows as a number |
| `DAYONE_PIN` | - | unlocks the device at start-up (needed for WhatsApp without a web login) |
| `WHATSAPP_*` | - | WhatsApp sandbox, see `WHATSAPP.md` |

## Layout

```
app/schema.py        field schema, statuses, blank-cell and dependency rules
app/normalize.py     typing (dates, numbers), plausibility, comparison
app/redaction.py     identifier scrubbing for values and logs
app/extractors/      Extractor interface; gemini.py, fake.py, DisabledExtractor
app/pipeline.py      raw model output -> typed fields with status and confidence
app/storage.py       SQLCipher database + AES-GCM image blobs, events, outbox
app/lifecycle.py     record states and allowed transitions
app/worker.py        "Pending AI processing" queue and sync queue
app/linking.py       candidate suggestions (never links, never creates)
app/conversation.py  the chat agent
app/i18n.py          every sentence of the agent, in English and French
app/languages.py     entries written in English or Arabic (digits, dates, "unknown", identifiers)
app/quality.py       on-device image-quality check
app/whatsapp.py      connector to the WhatsApp Business Cloud API
app/dashboard.py     anonymised aggregates for the dashboard
app/server.py        HTTP API + app/static/index.html (chat UI) + WhatsApp webhook
app/sync_server.py   simulated central server (separate process, idempotent) + dashboard
eval/                ground truth, split, degradation, evaluation, English/Arabic pages, replay
                     (never imported by app/)
scripts/             run_demo, check, run_eval (.py, with .sh wrappers), pack_submission.py, seed_dashboard.py
tests/               179 tests
```

## Design choices

**Schema first.** One section per page type of the form, defined in `app/schema.py`. The
extractor is asked for exactly these keys; anything else it returns is dropped.

| Section (page) | Fields | Repeating list |
|---|---|---|
| `cover` | 8 | - |
| `identification` | 43 | `previous_deliveries`: up to 5 x 6 fields |
| `pregnancy` | 6 | `visits`: up to 9 x 29 fields |
| `delivery` | 16 | - |
| `pp_early_mother`, `pp_late_mother` | 27 each | - |
| `pp_early_newborn`, `pp_late_newborn` | 22 each | - |

Antenatal visits and previous deliveries are lists. A column that is entirely empty produces
no entry.

**Field statuses** (exact names from the brief): `KNOWN`, `UNKNOWN`, `NOT_PROVIDED`,
`ILLEGIBLE`, `NOT_APPLICABLE`, `NEEDS_REVIEW`. Only `KNOWN` and `NEEDS_REVIEW` may carry a
value. `NEEDS_REVIEW` is not final: a record cannot be validated while any field has it.
There is no "N/A" value anywhere.

**Blank cells: `NOT_PROVIDED` versus `NOT_APPLICABLE`.**

1. A cell the midwife struck (a lone dash, slash or strike-through) is `NOT_APPLICABLE`.
2. A blank cell is `NOT_APPLICABLE` when another field, itself `KNOWN`, rules it out. The
   rules are listed in `DEPENDENCIES` in `app/schema.py`, for example: cesarean indication when
   the mode is not a cesarean; instrumental details when the mode is not instrumental;
   vaccination date when that vaccine box is "not ticked"; RAI when rhesus is Rh+; "reason
   for no contraception" when a method is wanted.
3. Every other blank cell is `NOT_PROVIDED`.
4. A written value is never overridden by rule 2.
5. Checkboxes are judged per group of boxes printed together on the form
   (`checkbox_groups` in `app/schema.py`). If no box in a group is ticked, every box in it
   is `NOT_PROVIDED`. If at least one is ticked, the others are `KNOWN` with the value
   "not ticked". The value is stored and shown as "ticked" / "not ticked", never as yes/no.
   A single-choice group with nothing chosen is always `NOT_PROVIDED`.

   | Page | Groups of several fields |
   |---|---|
   | Cover | high-risk box + risk types |
   | Identification | consanguinity + desired pregnancy; tetanus doses + rubella + hepatitis B vaccination |
   | Delivery | place (setting, supervised place, assisted at home); mode + instrumental details; complications (present, timing, types) |
   | Postpartum, mother | perineum + repaired; complications (present + list); family planning (wants, method, prescription, referred) |
   | Postpartum, newborn | premature + hypotrophic; vaccines today + vitamin D |

   Every other checkbox field is a group on its own. Consequence: a box that stands alone
   (uterine globe, cesarean, medication intake, transfer) is either "ticked" or
   `NOT_PROVIDED`; it can never be "not ticked".
6. "?" or "inconnu" written on paper is `UNKNOWN`.

**Never guess.** Values are stored as written. A value that cannot be typed (a date like
"2023") or is outside a plausibility range is kept verbatim and sent to review, never
corrected. Cross-field checks (parity versus gravidity, LMP versus expected date) only lower
trust.

**Confidence and follow-ups.** The model returns a confidence per field. Any field below the
threshold, any illegible field, any untypable or implausible value, and any value from which
an identifier was removed becomes a follow-up question. The agent states its reading, its
confidence and the reason.

**Always confirmed.** The linking code and every infection test result that is written (HIV,
syphilis, HBsAg) are put to the midwife whatever the confidence. Blank test cells are not
asked about. Rubella and toxoplasmosis serology are not in this list.

**Threshold.** The lowest value that keeps the "silent error" rate (wrong fields the agent
would not ask about) at or below 2%, chosen on the dev patients only and reported on the
test patients. Each run writes the curve of silent error rate versus share of fields asked
to the report and to `work/results/curve-*.csv`. If 2% needs more than 25% of filled fields
asked, or cannot be met, nothing is written: the run stops and prints the options. Until
`config/threshold.json` exists the app uses 0.85 and labels it provisional in the UI and chat.

**Privacy.**

- The schema has no field for the woman's name, husband's name, CIN, phone, address or
  examiner names. The prompt tells the model not to transcribe them, unknown keys are
  dropped, and every text value passes a pattern scrub (`app/redaction.py`) before storage.
- What the midwife types in the chat is scrubbed too, and logs go through the same filter.
- Location is stored as region, province and facility only. **The form has no village
  field**; the street address is dropped.
- Husband's occupation and the "examined by / seen by" names are not modelled. The midwife
  ID comes from the logged-in account.
- **Original images** are kept, as the brief requires, although they show names. They are
  stored only as AES-256-GCM blobs on the device, linked to record ID, capture date, midwife
  ID and status. Only the capturing midwife and supervisors can open them; data managers
  cannot; every access or refusal is audited. Images are never synchronised: the sync payload
  carries page metadata and a hash only.
- **The hosted model sees the whole photo, names included.** This is acceptable for the
  synthetic data only. A real deployment needs a local or formally approved model behind the
  same `Extractor` interface.
- Internal IDs are random UUID4 values.

**Encryption.** The database is SQLCipher; its key is derived from the device PIN with
scrypt. The image key is random and stored inside the encrypted database.

**Offline-first.** Capture, storage, review, manual entry, matching and registration need no
network. Only AI extraction and sync do, and both are queued in the encrypted database.

**Patient linking.** The linking code is "N° de la fiche" or a handwritten code, typed by the
midwife when unreadable. `app/linking.py` suggests candidates from near-identical codes and
agreement on age, province and LMP date. It never links and never creates. The midwife always
chooses among `[Patient 1] [Patient 2] [None, create new] [I'm not sure]`; "I'm not sure"
leaves the record unlinked in `MANUAL_REVIEW_REQUIRED`.

**Re-digitisation.** A new capture whose code equals a saved record's goes to
`DUPLICATE_SUSPECTED`. The agent shows the saved record and, page by page, the differing
fields; the midwife chooses new or saved values. The old record is kept and marked superseded.

## Record lifecycle

`CAPTURED -> PENDING_AI -> AI_PROCESSED -> NEEDS_REVIEW -> VALIDATED -> PATIENT_MATCHED -> REGISTERED -> SYNCED`

The brief describes four failure states without naming them; these names are ours:
`PROCESSING_FAILED`, `SYNC_FAILED`, `DUPLICATE_SUSPECTED`, `MANUAL_REVIEW_REQUIRED`.

| From | Allowed next states |
|---|---|
| `CAPTURED` | `PENDING_AI` |
| `PENDING_AI` | `AI_PROCESSED`, `PROCESSING_FAILED`, `NEEDS_REVIEW` (manual entry started) |
| `AI_PROCESSED` | `NEEDS_REVIEW`, `DUPLICATE_SUSPECTED`, `VALIDATED` (nothing uncertain and the midwife confirms) |
| `NEEDS_REVIEW` | `VALIDATED`, `CAPTURED` (retake), `PENDING_AI` (let the AI read after manual start) |
| `VALIDATED` | `PATIENT_MATCHED`, `MANUAL_REVIEW_REQUIRED` ("I'm not sure"), `NEEDS_REVIEW` |
| `PATIENT_MATCHED` | `REGISTERED` |
| `REGISTERED` | `SYNCED`, `SYNC_FAILED` |
| `SYNCED` | terminal |
| `PROCESSING_FAILED` | `PENDING_AI` (automatic retry), `MANUAL_REVIEW_REQUIRED` (after 3 tries) |
| `SYNC_FAILED` | `REGISTERED` (retry; manual after 5 tries) |
| `DUPLICATE_SUSPECTED` | `NEEDS_REVIEW` |
| `MANUAL_REVIEW_REQUIRED` | `NEEDS_REVIEW`, `PENDING_AI`, `CAPTURED`, `VALIDATED` |

Everything else is refused. Each transition and its audit event are written in one
transaction; the events table is append-only. Losing the network is never a failure: the
record stays queued and no retry budget is used. A page stays `PENDING_AI` until its result
is committed, and the server is idempotent on (record, version), so a lost acknowledgement
cannot duplicate a record.

**Review.** After reading, the agent lists what it read per page and asks about every field
it is unsure of (confidence under the threshold, illegible, untypable, implausible, or always
confirmed: linking code and infection test results). Each question shows the value read, the
confidence and the reason, and offers Correct / type the value / Blank / Unreadable / Not
applicable / Written as unknown / Skip. From three uncertain fields on, "See all N at once" shows
them in one numbered message: the midwife confirms the values shown, or types a number to correct
that field; fields without a readable value are then asked one by one. For each table the agent
asks whether another column is filled on paper. Only then is Confirm offered, next to Edit a
field, Retake a photo and Show all fields. Manual entry follows the same questions page by page.

**When the AI service is in trouble.** An overloaded service (HTTP 5xx), a used-up quota
(429), a refused key (401/403) or an unknown model (404) says nothing about the page, so the
page is not blamed: it stays `PENDING_AI` and uses none of its three attempts. The worker waits
10 s, then 20, 40, up to 60 s before asking again, and keeps synchronising meanwhile. The
midwife is told once per registry what is happening, with the provider's own explanation, and
can enter the data herself or tap "Try again now". The status box of the page shows the same
thing. Only a request the provider rejects as such (HTTP 400) or an unusable answer counts as
an attempt; after three, the registry goes to `MANUAL_REVIEW_REQUIRED` with the reason shown.

## Bonus features

**Image-quality check on the device** (`app/quality.py`). Each photo is saved first, then
checked without any network: shorter side under 600 px, paper level too dark, edges too soft
(blur), or ink barely darker than the paper. When something is found the agent says what and
offers "Retake this photo" or "Keep it anyway"; the midwife decides, and the first photo is
kept (superseded) when she retakes. Measured on the dataset: no warning on the 124 page images
and the 5 real photos, nor on the 20 shadow and 20 skew degradations; all 20 blur and all 20
low-light degradations are flagged. About 30 ms per photo. The thresholds were set on this
synthetic material and have not been tried on field photos.

**French / English interface** (`app/i18n.py`). Chat, buttons, side panel and login screen.
Switch with the EN / FR button or by typing *français* / *english*; the choice is stored per
account. In French, fields are named by their printed label. Stored values, the printed
options of the form and the lifecycle state names are never translated. There is no Arabic
interface.

**Anonymised dashboard** (`app/dashboard.py`, http://127.0.0.1:8001/dashboard). Counts of
synchronised registries; blood pressure and temperature in plain numeric ranges; HIV, syphilis
and Ag HBs results as written. A group smaller than 5 is shown as "< 5", and a second group is
hidden when only one is that small. No individual record, code, date or facility is shown, and
nothing is interpreted. `python scripts/seed_dashboard.py` loads the ten synthetic patients of
the dataset (their reference values, not an extraction) so that the page has content in a demo.
The simulated server has no accounts, so the page is not access-controlled.

**Arabic and multilingual entries** (`app/languages.py`). Arabic-script digits, month names
in the three languages and "unknown" words are typed like their French equivalents; phone
numbers in Arabic digits and names after English or Arabic titles are scrubbed. This is always
active. Telling the model itself that entries may be in French, Arabic or English and must be
copied in their own script is optional (`DAYONE_PROMPT_LANGUAGES=1`) and off by default, because
it lowered the measured accuracy on French. Reading accuracy on Arabic or English entries is not
measured with either prompt (no sample in the dataset).

**Real WhatsApp sandbox** (`app/whatsapp.py`, `WHATSAPP.md`). A webhook receives text, photos
and button taps from the WhatsApp Business Cloud API and hands them to the same agent; the
agent's messages, including later notifications, are delivered in order and retried after a
connection loss. Up to 3 options become reply buttons, up to 10 a list, more a numbered list
answered by its number. Only numbers listed in `WHATSAPP_NUMBERS` are served. Tested against a
simulated WhatsApp service only. Photos sent this way pass through Meta: synthetic data only.

## English and Arabic test pages

The dataset has French entries only. `python -m eval.make_languages` writes an English and an
Arabic version of each of its 80 pages to `work/languages/{en,ar}/`, with reference values in
`work/ground_truth/gt-{en,ar}.json` (also provided ready-made in `dayone-languages.zip`). On each
page the 1,745 handwritten entries that belong to schema fields are removed and written again in
the other language, at the same place, in a handwriting-style font; the printed form, the
checkboxes, the strike marks and the identifiers the app must ignore are untouched. Words come
from a translation table that covers every handwritten word of the dataset; numbers, dates and
blood pressures keep their value. In the Arabic set, patients 6 to 10 write their digits in
Arabic script.

```bash
GEMINI_API_KEY=... bash scripts/run_eval_languages.sh        # test patients, both languages, about 130 calls
```

Results (run by the team on 4 October 2026; same model, thinking level and prompt v1 as the
French run; dev = 48 pages, test = 32 pages per language):

| dev / test patients | French (dataset) | English (synthetic) | Arabic (synthetic) |
|---|---|---|---|
| Filled fields read correctly | 98.3% / 97.3% | 96.9% / 97.2% | 95.0% / 97.1% |
| Blank fields left blank | 99.1% / 99.4% | 99.4% / 100% | 100% / 100% |
| Values invented on blank cells | 0 / 0 | 0 / 0 | 0 / 0 |
| Filled cells returned as blank or illegible | 16 / 22 | 35 / 23 | 20 / 0 |
| Filled fields asked, at threshold 0.50 | 3.4% / 4.3% | 4.6% / 4.3% | 3.2% / 2.1% |
| Stored values that are wrong and were not asked, at 0.50 | 0.2% / 0.3% | 0.3% / 0.4% | **2.5% / 1.9%** |

- English behaves like French, including whole table columns that the model does not return
  (the agent asks about those).
- **What goes wrong in Arabic is the digits.** The model's saved answers were compared with the
  reference values. Of the 92 filled fields read wrongly on the Arabic pages (64 dev, 28 test), 53
  are numbers written with Arabic-script digits (2 and 3 confused above all: "٣٦٢٦" read 2396,
  "2026" read 2021), 15 are a table column that was not returned, 14 are misread words, 10 are
  checkboxes. No number written with Western digits on an Arabic page was misread. These are true
  misreadings, not spelling variants, and the model gives nearly all of them a confidence of 0.90,
  above the general threshold, so the app would store them without a question.
- The model returns Arabic-script digits as plain numbers, so the app cannot tell from a value
  which script it was written in. It can tell that a PAGE has entries in Arabic script.
- The app therefore has an **optional stricter threshold for pages with Arabic entries**, applied
  to their numbers (default) or to every filled field. Replayed from the saved answers through the
  app's own code (`python -m eval.replay`, no model call):

| Arabic pages, dev / test patients | Wrong values stored without asking | Filled fields asked |
|---|---|---|
| Option not set (threshold 0.50) | 2.5% / 1.9% | 3.2% / 2.1% |
| Numbers below 0.92 are asked | 0.6% / 1.2% | 14.4% / 19.2% |
| Every filled field below 0.92 is asked | 0.5% / 0.4% | 33.0% / 56.7% |

  The first variant meets, on the dev patients, the rule used for the French threshold (at most
  2% wrong values stored unasked, at most 25% of filled fields asked); the second asks too much.
  French and English pages are unaffected by either. Set it with
  `python -m eval.evaluate --set-arabic-threshold 0.92` (add `--arabic-scope all` for the second
  variant; `0` removes it). It is stored in `config/threshold.json` and is not set by default.
- A page whose only Arabic content is digits, with no Arabic word, is not recognised as an Arabic
  page (2 of the dev errors).

Limits: this is not real handwriting. The Arabic is set in handwriting-style typefaces, far
more regular than a midwife's hand, and the translations are the tool's. A score on these pages
shows whether the pipeline copes with the languages and scripts, not how it will do on real
Arabic handwriting. Checked without a model: when the reference values are fed back as if read
(Arabic digits and all), the typing, scrubbing and scoring code returns 100% on both languages.

## Evaluation method

- **Ground truth.** The organisers' CSV does not describe the images (aggregate columns; no
  row matches the 10 specimen patients). Ground truth is built from the registry PDF: the
  handwritten glyphs of the text layer, and checkbox state from the vector drawing
  (`eval/build_ground_truth.py`). It covers every schema field on all 80 pages. It is read by
  `eval/` only; a test asserts that `app/` never references it.
- **Known quirks of the data.** Some handwriting fonts lack "é" and "—": the image shows a
  gap. Such a character is a wildcard in comparison (58 fields), and a cell whose only
  content is an undrawn dash is treated as blank (93 cells).
- **Images.** 129 files, 85 distinct by hash; the 80 distinct specimen pages are used.
- **Split.** By patient, seed 17: dev = patients 2, 3, 5, 7, 9, 10; test = 1, 4, 6, 8.
- **Degraded set.** One synthetic degradation per page (blur, shadow, skew, low light; seed
  2026) written to `work/degraded/`. Reported separately and labelled synthetic.
- **Metrics.** Accuracy on filled fields and on blank fields separately; invented values;
  omissions; by section, by mark type (handwritten, checkbox, blank), by handwriting style,
  by degradation; confidence versus correctness; threshold curve.
- **Language.** The form is printed in French; entries in French, English or Arabic are accepted
  (Arabic-script digits, month names and "unknown" words in the three languages, identifier
  scrubbing for English and Arabic titles, phone numbers and street addresses: see
  `app/languages.py`). Only French has been measured. The dataset contains no Arabic or English entries, and no
  printed (typed) values: every filled value is handwriting or a checkbox mark.
- **Real photos.** The 5 phone photos have no reference values; `--photos` dumps the
  extraction for inspection by eye.

## Known limitations

- Extraction quality is measured for French entries on clean images only (see "Results").
  Degraded images, the real photos, and Arabic or English entries are unmeasured.
- The reason why the language rules lower the accuracy on French tables is not known.
- In the build environment the Gemini extractor has only run against a mocked HTTP layer and
  a local stand-in server; the live runs were made by the team on their own machine.
- Pattern-based redaction cannot recognise a bare personal name inside a free-text field,
  and its patterns are tuned to Moroccan CIN and phone formats.
- **Mismatch with the brief: hepatitis.** The brief lists hepatitis C among the infection
  tests. The form has no hepatitis C row; it has "Ag HBs", the hepatitis B surface antigen.
  The schema stores that row under its real name, `hbs_antigen`, and has no hepatitis C
  field. Nothing is labelled hepatitis C.
- **Free-text "Autres" lines are not stored** (cover, delivery and postpartum-mother pages):
  redaction cannot catch a bare name written in them. Husband's occupation is not stored.
  The two "Autres à préciser" lines of the newborn pages (`danger_signs_other`, `trauma_other`)
  are still stored; whether to drop them too is an open decision.
- "Grossesse classée à risque" and its risk types are transcribed as written on paper; the
  system computes nothing from them.
- One page per page type per record: a second photo of the same page type replaces the
  AI-read fields of the first (midwife-entered fields are kept).
- With the AI path, a new visit column can only be added through "Edit a field" then "add".
- "See all at once" confirms the listed values with one tap; it is as careful as the midwife
  who taps it. A side-by-side screen (photo next to the fields) does not exist.
- The WhatsApp connector has never been run against the real service. WhatsApp only allows
  free-form messages within 24 hours of the midwife's last message; a notification that
  becomes ready later than that is refused by WhatsApp and stays queued.
- The image-quality thresholds come from synthetic degradations; glare and partial pages are
  not detected.
- The dashboard hides small groups but is not access-controlled on the simulated server.
- An interrupted capture can leave an empty record in `CAPTURED`.
- One device PIN for all accounts; accounts are seeded and have no individual passwords.
- Connectivity and the central server are simulated. Unless the WhatsApp sandbox is configured,
  WhatsApp is simulated by the web chat. The interface exists in French and English, not Arabic.
- Degradations are synthetic and milder than the 5 real photos.
