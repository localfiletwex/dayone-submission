# Changes in this update (4 October 2026)

Starting point: the folder as it was on the team's laptop (135 tests, plus the language update
and the login fix applied with the two updater scripts).

## How it was tested

| Check | Result |
|---|---|
| Automated tests | 172 pass (was 139 passing and 1 failing) |
| Real browser, real app, stand-in model: login, capture of dataset pages, review, matching, sync, offline and back, 503 / 400 / 401 from the model, image-quality question, switch to French, phone width, roles, dashboard | 34 of 34 checks pass |
| WhatsApp connector through the running server, against a simulated WhatsApp service; dashboard with the ten synthetic patients | 32 of 32 checks pass |
| Whole evaluation script against a stand-in model that returns the reference values | runs to the end, 100% (the scoring and typing code does not alter French values) |
| Device database created by the previous version, opened by this one | review continues where it was |
| Tests run with a key, a model name, a PIN and WhatsApp settings exported in the shell | same result |

**Not tested, because the build sandbox cannot reach them:** live Gemini calls, and the real
WhatsApp service. Run `GEMINI_API_KEY=... bash scripts/check.sh` for one live call.

## Fixes

- **A registry no longer waits silently when the AI service is in trouble.** Overload (5xx),
  quota (429), refused key (401/403) and unknown model (404) leave the pages queued without
  using their attempts. The chat says why, once per registry, with the provider's own
  explanation; the status box shows the next automatic try and a "Try again now" button.
  Before, the app retried for about two minutes at a time without a word, and nothing else
  (including synchronisation) ran meanwhile.
- **Errors show the provider's explanation**, not only "HTTP 400".
- **A refused key or unknown model no longer burns the three attempts of a page.** This
  includes the way Gemini actually reports a wrong key: HTTP 400 "API key not valid".
- **Test that failed on the laptop** (`test_model_and_thinking_level...`): it read
  `GEMINI_MODEL` from the shell. Tests now ignore everything exported in the shell.
- **Test that failed about once in 140 runs** (`test_patient_ids_do_not_derive_from_code`): it
  required that a random identifier not contain "AAA".
- **Login screen**: the fields stay inside the card (now part of the page, not a patch).
- **Pending message** says the real reason: no connection, AI switched off, or no API key.

## Decision changed: default model

`GEMINI_MODEL` now defaults to `gemini-3.5-flash-lite` instead of `gemini-3.8-flash`, because
the evaluation in `work/results/` and the threshold in `config/threshold.json` were produced
with it. To go back: set `GEMINI_MODEL=gemini-3.8-flash`, or change the one line in
`app/config.py`, and rerun the evaluation.

## Bonuses

| Bonus | Where | Notes |
|---|---|---|
| Image-quality check on the device | `app/quality.py` | Saves first, then warns; "Retake this photo" / "Keep it anyway" |
| French / English interface | `app/i18n.py`, `app/static/index.html` | EN / FR button, or type `français` / `english` |
| Anonymised dashboard | `app/dashboard.py`, http://127.0.0.1:8001/dashboard | `python scripts/seed_dashboard.py` fills it with the ten synthetic patients |
| Arabic and multilingual entries | `app/languages.py` | Folded into the normal code; accuracy not measured |
| Real WhatsApp sandbox | `app/whatsapp.py`, `WHATSAPP.md` | Needs a Meta developer account; never run against the real service |

## Other additions

- Photos can be dropped on the chat (laptop).
- Status box: model in use, threshold and where it comes from, a warning when the model differs
  from the one the threshold was calibrated with.
- Records waiting for the AI show "2 of 7 page(s) read".
- `scripts/check.sh`: one command that checks the installation.
- README: measured results, the new features, updated limitations.

## Later the same day: the language rules in the prompt are now off by default

The evaluation rerun on the laptop with the language rules in the prompt (v2) gave 90.0% of
filled fields on the dev patients, against 98.3% with the earlier prompt (v1), and no threshold
met the 2% target. The default prompt is therefore v1 again, word for word; the rules can be
switched on with `DAYONE_PROMPT_LANGUAGES=1`. The threshold of 0.50 was calibrated with v1 and
is valid for the default again. Reports and `config/threshold.json` now record the prompt version.

## Review against the scoring criteria (see CRITERIA.md)

- **A table column the model does not return is no longer silent.** Analysis of the saved
  evaluation rows showed that most reading errors are whole columns of the previous-deliveries
  table that the model omits; the app stored such a registry without asking. For every table
  the agent now says how many columns it read and asks whether another is filled.
- **All uncertain fields in one message** ("See all N at once"), with one tap to confirm the
  values shown or a number to correct one.
- "That was the last question" is only said when nothing is left to review.
- Unused imports removed; the code passes a static check (pyflakes) with no remark.
- Checked again on the final code: 172 automated tests; the browser run (36 checks); the WhatsApp
  and dashboard run (32 checks); the app killed abruptly during an offline capture and restarted
  (registry, pages, chat and images intact, reading resumes).

## English and Arabic test pages

- `eval/make_languages.py` makes an English and an Arabic version of the 80 dataset pages, with
  reference values; `scripts/run_eval_languages.sh` measures the model on them;
  `python -m eval.evaluate --language en|ar` is the underlying option.
- **Facility names were masked as addresses** when they contain a neighbourhood ("CSC Hay
  Salam", "CSUA Hay Riad"), and the midwife could not type them back. Fixed for that field.
  This touches 2 fields of the dev patients, so the dev accuracy in the README (98.3%) may move
  by about 0.1 point at the next run of `scripts/run_eval.sh --clean-only`.

- Measured by the team on the test patients: English 97.2%, Arabic 97.1% of filled fields; wrong
  values stored without asking: 0.4% and 1.9% (French 0.3%). Details in the README.

- Dev patients measured too (English 96.9%, Arabic 95.0%). In Arabic, 2.5% of stored values are
  wrong without having been asked.
- The model's saved answers show why: numbers written with Arabic-script digits are misread
  (53 of the 92 Arabic errors), with a confidence of 0.90. Words and Western digits are read well.
- Optional stricter threshold for the numbers of pages that have Arabic entries, off by default:
  `python -m eval.evaluate --set-arabic-threshold 0.92`. Replayed from the saved answers: 0.6%
  (dev) and 1.2% (test) wrong values stored unasked, for 14% and 19% of filled fields asked.
  (A first version of this option looked at the script of each value; it could not work, because
  the model returns Arabic digits as plain numbers.)
- `python -m eval.replay` replays the saved answers through the app's own code, without any model call.

## Running it anywhere, and handing it in

- `pip install -r requirements.txt` failed on macOS and Windows (the encrypted-database package
  ships for Linux only). Other systems now get an equivalent package; it passes the tests on
  Linux and has not been run on macOS or Windows.
- The start, check and evaluation scripts are now Python (`scripts/run_demo.py`, `check.py`,
  `run_eval.py`); the `.sh` commands remain as wrappers. No bash is needed.
- `python scripts/pack_submission.py` writes one self-contained zip: code, threshold, results,
  saved model answers, English and Arabic pages, and the dataset. It then checks the contents,
  looks for anything resembling an API key, unpacks the zip in a temporary folder, runs the
  tests there and reproduces the French test results without a key.
- README: a quick start for reviewers at the top.

## Still open

- Arabic: decide whether to set the stricter threshold.

- The rerun overwrote `work/results/eval-dev-...-clean.md` with the v2 numbers. Running
  `scripts/run_eval.sh --clean-only` again rewrites it for v1 from the cache (no new model
  calls for pages already read).
- Accuracy on degraded images, on the real photos, and on Arabic or English entries is not measured.
- The two free-text "Autres à préciser" lines of the newborn pages are still stored.
- The bulk review screen is not built.
- Files left by the earlier updater scripts (`backup_before_languages/`,
  `languages_update_tests.txt`, `app/static/index.html.before_login_fix`) are no longer needed
  and can be deleted.
