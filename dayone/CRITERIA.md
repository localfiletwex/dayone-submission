# The prototype against the scoring criteria

One section per criterion of the brief (section 7): what is asked, where it is in the code, what
the evidence is, and what is still weak. Numbers come from `work/results/` (model
`gemini-3.5-flash-lite`, thinking level `low`, prompt v1, clean images, French entries).

## 1. Extraction quality (30 points)

*Field-level accuracy on a test set, handwritten and printed text (French, Arabic, English).*

| | Dev patients | Test patients |
|---|---|---|
| Filled fields read correctly | 98.3% (n=1274) | 97.3% (n=956) |
| Blank fields left blank | 99.1% | 99.4% |
| Values invented on blank cells | 0 | 0 |
| Page type recognised | 48 / 48 | 32 / 32 |

- Code: `app/extractors/gemini.py` (prompt built from the schema), `app/pipeline.py`, `app/normalize.py`.
- Method: `eval/` builds reference values from the registry PDF, splits by patient (threshold set
  on dev, reported on test), and never feeds reference values to the extractor (tested).
- **Weak:** 21 of the 26 test errors are previous-deliveries columns that the model does not
  return. The agent asks about them (criterion 2), but the model still does not read them.
- English and Arabic, on synthetic pages made from the same patients (not real handwriting):
  97.2% and 97.1% of filled fields on the test patients.
- **Not measured:** real Arabic or English handwriting, degraded images, the five real photos
  (no reference values), printed values (the dataset has none).

## 2. Uncertainty handling (20 points)

*Correct field statuses, meaningful confidence scores, an agent that never hides its doubts.*

- Six statuses per field: `KNOWN`, `UNKNOWN`, `NOT_PROVIDED`, `ILLEGIBLE`, `NOT_APPLICABLE`,
  `NEEDS_REVIEW` (`app/schema.py`). Blank, struck-through and "unknown" cells are distinct, and
  "not ticked" is never stored as "no".
- A field is asked about when its confidence is under the threshold, when it is illegible,
  cannot be typed, is outside its plausible range, fails a cross-check, or is a linking code or
  an infection test result (always confirmed).
- Threshold 0.50, chosen on dev for at most 2% of wrong values stored without asking. Measured:
  0.2% on dev, 0.3% on test, asking about 3.4% and 4.3% of filled fields.
- For every table the agent asks whether a column is missing, so an omitted column is not silent.
- When the AI service fails, the agent says why instead of waiting silently.
- **Weak:** on the synthetic Arabic pages, 2.5% (dev) and 1.9% (test) of stored values are wrong
  without having been asked (French 0.2% and 0.3%). The cause was examined in the model's saved
  answers: numbers written with Arabic-script digits are misread (2 and 3 above all) with a
  confidence of 0.90. An optional stricter threshold for the numbers of pages with Arabic entries
  brings this to 0.6% (dev) and 1.2% (test) while asking about 14% and 19% of the filled fields;
  it is off until it is set (`--set-arabic-threshold 0.92`).
- **Weak:** the model's confidences sit almost all at 0.95 or 1.0, so the threshold separates
  little between 0.50 and 0.95; the low silent-error rate comes mostly from the model's accuracy
  and from the rules above, not from fine-grained confidence.

## 3. Conversational review workflow (20 points)

*Clarity of the Confirm / Edit / Retake flow, follow-up questions, manual entry, multi-page sessions.*

- Code: `app/conversation.py`; wording in `app/i18n.py` (English and French).
- Multi-page sessions (send pages, then Done); summary per page; follow-up questions with the
  value read, the confidence and the reason; "See all N at once"; Confirm only when nothing is
  left; Edit a field; Retake a photo (old image kept, midwife's values kept); Show all fields;
  manual entry field by field with "Rest of this page is blank" and "Finish later";
  re-digitisation of a known registry with a field-by-field comparison.
- Evidence: `tests/test_conversation.py`, `tests/test_i18n.py`, and a run in a real browser.
- **Weak:** it is a scripted dialogue: it does not understand free sentences. There is no screen
  showing the photo next to the fields.

## 4. Offline robustness (15 points)

*No record ever lost; queue, states and synchronization behave correctly when connectivity drops and returns.*

- A photo is written to encrypted storage before the agent answers. Processing and
  synchronisation are queues in the same database (`app/worker.py`, `app/storage.py`); every
  state change is one transaction and is recorded in the audit trail (`app/lifecycle.py`).
- Offline, pages wait as `PENDING_AI` and manual entry stays possible; registered records wait in
  the outbox. Synchronisation is idempotent on (record, version) and retried.
- Evidence: `tests/test_offline_queue.py`, `tests/test_ai_status.py`; the app killed with
  SIGKILL during an offline capture and restarted (registry, pages, chat and image intact,
  reading resumed).
- **Weak:** connectivity and the central server are simulated on one machine.

## 5. Patient linking and privacy (10 points)

*Code-based linking of visits, possible-match handling, no direct identifier stored, original image with restricted access.*

- Linking by the registry code; candidates are only suggested, with the reasons; the midwife
  chooses an existing patient, a new one, or "I'm not sure" (nothing is created then)
  (`app/linking.py`). Patient ids are random.
- No field for name, husband's name, national ID, phone or address exists in the schema; the
  prompt forbids transcribing them; typed and extracted text is scrubbed (`app/redaction.py`),
  in French, English and Arabic; logs are scrubbed too.
- Database encrypted with SQLCipher, images with AES-GCM, key derived from the device PIN.
  Images are readable by their midwife and by the supervisor only, never synchronised, and each
  access is logged (`tests/test_storage_privacy.py`).
- **Weak:** a bare name written inside free text cannot be recognised by patterns. Two free-text
  lines of the newborn pages are still stored (open decision; the other free-text lines are not).

## 6. Code quality and documentation (5 points)

*Readability, structure, README, reproducibility.*

- 179 automated tests, independent of the shell they run in; `scripts/check.sh` checks an
  installation in one command; `scripts/run_eval.sh` reproduces the evaluation (seeded split and
  degradations, cached model answers); static check without remarks.
- `README.md` (setup, design, lifecycle, results, limitations), `DEMO.md`, `WHATSAPP.md`,
  `CHANGES.md`.

## Deliverables and bonuses

- Demo (`DEMO.md`): offline capture, return of connectivity, review of an uncertain field, match decision.
- Bonuses, all optional in the brief: image-quality check, French / English interface, anonymised
  dashboard, Arabic and multilingual entries (accuracy not measured), WhatsApp connector (tested
  against a simulated service only).
