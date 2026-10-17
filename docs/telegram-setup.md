# Telegram Integration Setup

GuardianBox can send real-time alerts to family members via Telegram when a fall is detected and the resident doesn't respond to voice check-ins.

## Overview

- **Privacy-first**: Only text alerts are sent. No photos or video ever leave the device.
- **Inline buttons**: Family can respond directly from the Telegram message (Calling, On my way, False alarm, 999 info).
- **No webhook required**: Uses long-polling with `getUpdates`, so no public URL is needed.
- **Mock mode**: When no token is set, the dashboard shows a phone-style preview of what would be sent.

---

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "GuardianBox Alert")
4. Choose a username (e.g., `guardianbox_alert_bot`)
5. BotFather will give you a token like:
   ```
   123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
   ```
6. **Keep this token secret** — anyone with it can send messages as your bot.

---

## Step 2: Get Your Chat ID

1. Open a chat with your new bot
2. Send `/start` to the bot
3. The bot will reply with your chat ID, like:
   ```
   Your chat ID is: 987654321
   ```

**Alternative method** (if the bot doesn't respond to /start yet):

1. Send any message to the bot
2. Open this URL in your browser (replace `YOUR_BOT_TOKEN`):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
3. Look for `"chat":{"id":987654321}` in the response
4. That number is your chat ID

---

## Step 3: Configure GuardianBox

On your DGX Spark, edit the `.env` file:

```bash
cd ~/guardian-box
nano .env
```

Add these lines (using your actual token and chat ID):

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_IDS=987654321
```

For multiple family contacts, separate chat IDs with commas:

```env
TELEGRAM_CHAT_IDS=987654321,123456789,555555555
```

**Important**: Never commit `.env` to git. It's already in `.gitignore`.

---

## Step 4: Restart the Services

```bash
cd ~/guardian-box
docker compose down
docker compose up -d
```

Or restart just the affected services:

```bash
docker compose restart orchestrator notifier
```

---

## Step 5: Test the Integration

### From the Dashboard

1. Open the dashboard at `http://<spark-ip>:8080`
2. Click **"📱 Test Message"** in the Contacts panel
3. Check your Telegram — you should receive:
   ```
   🧪 Test message from GuardianBox
   
   If you receive this, Telegram alerts are working!
   ```

### From the Command Line

```bash
curl -X POST http://localhost:8080/api/v1/telegram/test
```

Expected response:

```json
{
  "status": "sent",
  "sent": 1,
  "total": 1,
  "results": [{"chat_id": "987654321", "ok": true}]
}
```

---

## Step 6: Run the Full Demo

1. Open the dashboard at `http://<spark-ip>:8080`
2. Click **"🚨 Fall"** in the demo controls
3. Watch the pipeline timeline progress:
   - Pose trigger fires
   - VLM confirms fall
   - Voice check-in asks "Are you okay?"
   - After silence, alert is sent
4. Check your Telegram — you should receive an alert with buttons:
   - 📞 Calling
   - 🏃 On my way
   - ✅ False alarm
   - 🆘 999 info
5. Tap a button to acknowledge
6. The dashboard timeline should update with your response

---

## Mock Mode (No Token)

If `TELEGRAM_BOT_TOKEN` is not set, the dashboard shows a "Telegram Preview" panel on the right. This displays exactly what would be sent to Telegram, with clickable buttons that drive the same acknowledgement flow.

This is useful for:
- Demos without Telegram setup
- Testing the UI flow
- Understanding what family members see

---

## Alert Message Format

When an alert is sent, family receives:

```
🚨 URGENT

Ahmad · Living room · 14:35:02 (MYT)

What GuardianBox saw: Person on the floor, not moving.
What it tried: Asked 3 times. No reply.

Please call now. If it's an emergency, call 999.

(No photos or video are ever sent.)

[📞 Calling] [🏃 On my way]
[✅ False alarm] [🆘 999 info]
```

---

## Callback Buttons

When family taps a button:

| Button | Action | Result |
|--------|--------|--------|
| 📞 Calling | `calling` | Marked as responded, event resolved |
| 🏃 On my way | `on_my_way` | Marked as responded, event resolved |
| ✅ False alarm | `false_alarm` | Marked as false positive, event resolved |
| 🆘 999 info | `call_999_info` | Shows emergency services info (Malaysia 999) |

Button presses are received via long-polling `getUpdates` and mapped to the `/api/v1/demo/ack` endpoint, updating the dashboard in real-time.

---

## Troubleshooting

### "Failed to send test message"

1. Check your token is correct (no extra spaces)
2. Verify the bot exists by messaging it in Telegram
3. Check the orchestrator logs:
   ```bash
   docker logs orchestrator 2>&1 | tail -50
   ```

### "Chat ID not found"

1. Make sure you've messaged the bot first
2. Try the `/start` command again
3. Use the `getUpdates` URL to find your chat ID manually

### "Unauthorized"

The token is invalid. Go back to @BotFather and check it.

### "Bad Request: chat not found"

The chat ID is wrong, or you haven't messaged the bot yet. Each user must send at least one message to the bot before it can message them.

---

## Security Notes

- **Never share your bot token** — treat it like a password
- **The token is not logged** — we sanitize it in logs
- **Messages go through api.telegram.org only** — allowed in egress proxy
- **No photos are sent** — only text alerts, for privacy
- **Consider a dedicated bot** for GuardianBox, not a shared one

---

## Multiple Devices

If you have GuardianBox deployed on multiple Spark devices (e.g., different rooms or homes), you can:

1. Use the same bot for all devices
2. Use different chat IDs per device
3. Include the device/room name in the alert message (configured in `RESIDENT_NAME` and `RESIDENT_ROOM`)

---

## API Reference

### POST /api/v1/telegram/test

Send a test message to all configured chat IDs.

**Response:**
```json
{
  "status": "sent",
  "sent": 1,
  "total": 1,
  "results": [{"chat_id": "123", "ok": true}]
}
```

### GET /api/v1/health

Check if Telegram is configured.

**Response:**
```json
{
  "status": "healthy",
  "telegram_connected": true,
  ...
}
```
