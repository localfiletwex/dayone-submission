# WhatsApp sandbox (bonus)

The same agent that answers in the web chat can answer on WhatsApp, through the WhatsApp
Business Platform (Cloud API). Code: `app/whatsapp.py`.

**Status: tested against a simulated WhatsApp service only.** The connector has never been run
against the real one. Expect to adjust details the first time (the Graph API version, for
example). The web chat keeps working whether or not WhatsApp is configured.

**Privacy.** Photos sent through WhatsApp pass through Meta's servers, a third party. Use the
synthetic pages only. Phone numbers are read from the environment and are never written to the
database or the logs.

## What you need

1. A Meta developer account and an app with the **WhatsApp** product added
   (https://developers.facebook.com). The app gives you a test phone number.
2. From the app's WhatsApp > API Setup page:
   - the **temporary access token** (valid about a day) or a permanent system-user token;
   - the **Phone number ID** of the test number;
   - your own phone added to the list of allowed recipients.
3. A public HTTPS address that forwards to port 8000 of your laptop, for example a tunnel
   (`cloudflared tunnel --url http://127.0.0.1:8000`, or ngrok).
4. A word of your choice as the **verify token**.

## Start the app with WhatsApp

```bash
GEMINI_API_KEY=... \
DAYONE_PIN=1234 \
WHATSAPP_TOKEN=<access token> \
WHATSAPP_PHONE_NUMBER_ID=<phone number id> \
WHATSAPP_VERIFY_TOKEN=<your verify token> \
WHATSAPP_NUMBERS="<your number, digits only, with country code>=midwife1" \
bash scripts/run_demo.sh
```

| Variable | Meaning |
|---|---|
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` | all three are required; without them the connector is off |
| `WHATSAPP_NUMBERS` | who may talk to the agent: `number=account`, comma-separated. Accounts are the midwife accounts (`midwife1`, `midwife2`). Other numbers are refused. |
| `DAYONE_PIN` | unlocks the encrypted device database at start-up. Without it, someone must log in once in the web app before WhatsApp messages can be handled. It must be the PIN the device was created with. |
| `WHATSAPP_APP_SECRET` | optional. When set, every webhook call must carry a valid `X-Hub-Signature-256`. Recommended. |
| `WHATSAPP_API_VERSION` | Graph API version, default `v25.0`. |

## Register the webhook

In the Meta app, WhatsApp > Configuration > Webhook:

- Callback URL: `https://<your public address>/whatsapp/webhook`
- Verify token: the word you chose
- Subscribe to the **messages** field.

Meta calls the URL once to verify it; the app answers when the token matches.

## Use it

From the registered phone, send `help`, then a photo of a registry page, then tap **Done**.
Type `français` to switch to French. Send photos as documents if WhatsApp's compression makes
them hard to read (the image-quality check will say so).

How the chat maps to WhatsApp:

| In the web chat | On WhatsApp |
|---|---|
| up to 3 short buttons | reply buttons |
| up to 10 buttons | a list ("Choose") |
| more than 10 | a numbered list; reply with the number |
| a long listing | split into several messages |

## Limits

- WhatsApp accepts free-form messages only within 24 hours of the user's last message. A
  notification that becomes ready later is refused by WhatsApp and stays queued until the
  midwife writes again.
- The temporary token expires; the app log then shows "send refused, HTTP 401".
- One account per phone number; the web chat of the same account shows the same conversation.
- The demo's "Connectivity" switch also holds back WhatsApp messages while it is off.
