# Demo script (about 4 minutes)

Shows, in order: an offline capture, the return of connectivity, review of an uncertain
field, and a match decision. The right-hand panel shows queues, record states and the audit
trail live.

## Before

```bash
rm -rf device_data server_data            # fresh device and server (demo data only)
GEMINI_API_KEY=... bash scripts/run_demo.sh
```

Open http://127.0.0.1:8000, choose `midwife1`, set a PIN. Choose the language with the
**EN / FR** button (login screen or header); the steps below quote the English wording.

Have ready, from `data/data/Paper Registry/`: the cover and identification pages of patient 1
(`dossiers_specimen_10_patientes-01.png` and `-02.png`), and the cover page again for step 9.
On a laptop, send a page with the camera button or by dropping the file on the chat.
(Pages 01 to 06 have exactly these names; many other pages have a suffix after the number.)

Without a key, use `DAYONE_EXTRACTOR=fake`: the flow is identical but values are invented
and the fake finds no registry code, so the agent asks you to type one in step 4.

## 1. Offline capture

1. Switch **Connectivity** off. The header shows "offline".
2. Send the cover photo, then the identification photo (camera button).
   Each is acknowledged as "saved on this phone (encrypted)". Panel: record `CAPTURED`.
3. Tap **Done - no more pages**.
   The agent answers "Pending AI processing ... Nothing is lost" and offers manual entry.
   Panel: `PENDING_AI`, "Pending AI processing: 1", server records unchanged.

Point out: `device_data/` holds only an encrypted database and encrypted blobs.

## 2. Connectivity returns

4. Switch **Connectivity** on.
   Within a second or two the audit trail shows `PENDING_AI -> AI_PROCESSED -> NEEDS_REVIEW`
   and the agent posts its summary: fields read, fields to check, blanks per page.

## 3. Review of an uncertain field

5. The agent says how many fields it is not sure about and asks about each: what it read,
   its confidence, and why. The registry code and any infection test result are always
   asked, whatever the confidence. Answer one of each kind:
   - **It is correct** to confirm a reading;
   - type a corrected value (try an impossible one, such as age 131, to see it questioned);
   - **Unreadable on paper** or **Blank on paper** for an illegible field.
6. At the menu, show **Show all fields**, then **Edit a field**, then **Retake a photo**
   (optional). **Confirm** is only offered when nothing is left to review.
7. Tap **Confirm**. Panel: `VALIDATED`.

## 4. Match decision

8. First registry: the agent finds no similar patient and asks. Tap **Create new patient**.
   Panel: `PATIENT_MATCHED -> REGISTERED -> SYNCED`, server records: 1.
9. Send the cover page of the same registry again and tap Done.
   The agent reports a **possible duplicate**, shows the saved record and asks whether to
   update it or treat it as a different woman. Choose **a different woman** to reach the
   match question: `[Patient 1] [None, create new] [I'm not sure]` with the reasons
   ("same code"). Nothing is linked or created until a button is tapped.
10. Tap **Patient 1**, then type `history` to show both registries under one profile.

## Optional extras

- **The AI service is down:** nothing to simulate with a real key, it happens. The chat says
  why the registry is still pending (for example "the AI service is overloaded", with the
  provider's own text) and the **AI status** box shows the next automatic try and a
  **Try again now** button. Synchronisation continues meanwhile.
- **Image-quality check:** send a blurred or very dark photo (for example one from
  `work/degraded/`, `page-04-blur.jpg`). The agent saves it, says what is wrong and offers
  **Retake this photo** or **Keep it anyway**.
- **French interface:** tap **FR**, or type `français`. Everything new is in French; fields are
  named by their printed label.
- **Dashboard:** run `python scripts/seed_dashboard.py` once (loads the ten synthetic patients
  of the dataset), then open http://127.0.0.1:8001/dashboard, or tap **Open the anonymised
  dashboard** in the side panel. Small groups show as "< 5".
- **WhatsApp:** see `WHATSAPP.md`. It needs your own Meta developer account and has not been
  run against the real service.

- **AI unavailable:** switch **AI extraction** off, capture a page, tap **Enter manually
  now**, choose the page, answer a few fields, **Rest of this page is blank**.
- **Role-based access:** log in as `data1`: structured states are visible, images are not.
- **Restart safety:** stop and restart `run_demo.sh` mid-review; log in again and the chat
  resumes at the same question.
