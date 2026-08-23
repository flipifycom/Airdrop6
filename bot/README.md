# AirdropHunter AI — Monitoring Bot

24/7 Pyrogram userbot that monitors Telegram groups for airdrop announcements,
scores them with Groq (free), and acts per the dashboard's autonomy mode.
Communicates with your Base44 dashboard through the public REST API — no
backend functions, no integration-credit dependency.

## Files
- `bot.py` — the bot
- `requirements.txt` — Python deps
- `.env.example` — all config (copy to `.env` locally)
- `../Dockerfile` — root image used by Koyeb (builds only the bot)

## 1. Generate a Pyrogram session string
Run once (Termux, Colab, or any Python shell):

```bash
pip install pyrogram tgcrypto
python - <<'PY'
from pyrogram import Client
c = Client("gen", api_id=API_ID, api_hash="API_HASH", in_memory=True)
with c:
    print("SESSION:", c.export_session_string())
PY
```
Enter your phone + login code when prompted. Copy the printed SESSION value
into TG_SESSION.

## 2. Get your Base44 API values
- **App ID** — from the editor URL: …/apps/<APP_ID>/editor
- **API key** — Workspace Settings → Preferences → API Key
- **API base** — open Dashboard → API, pick an entity, choose Python snippet,
  and copy the base host shown there (default https://www.base44.com).

## 3. Deploy on Koyeb
1. Push this repo (bot/ + root Dockerfile) to GitHub.
2. In Koyeb → Create Service → GitHub → pick that repo.
3. Koyeb detects the root Dockerfile and builds only the bot.
4. Add these environment variables (secrets never touch the database):

```
TG_API_ID, TG_API_HASH, TG_SESSION
TG_BOT_TOKEN (optional), NOTIFY_CHAT_ID (optional)
GROQ_API_KEY, GROQ_MODEL
BASE44_APP_ID, BASE44_API_KEY, BASE44_API_BASE
DEFAULT_AUTONOMY, CONFIG_REFRESH_SECONDS
```
5. Deploy. The service runs as a long-running process; Koyeb keeps it alive 24/7.

## Autonomy modes (read live from BotSetting on the dashboard)
- full_autonomy — auto-sends /start etc. on every YES signal.
- human_in_loop — logs YES signals; you approve from the dashboard.
- logging_only — detects & logs only, never acts.

Change the mode in the dashboard Settings and the bot picks it up on the next
config refresh (every CONFIG_REFRESH_SECONDS).
