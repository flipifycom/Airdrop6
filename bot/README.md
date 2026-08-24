# AirdropHunter AI — Monitoring Bot (FULL AUTO + DAILY TASKS)

24/7 Pyrogram userbot. In Full Autonomy mode it: joins referenced channels/bots,
sends /start commands, auto-taps inline buttons (Join/Verify/Claim/Start), repeats
daily tasks (/tap /daily /claim) every day on its own, and — if a step needs a real
human (wallet connect, KYC, captcha, payment) — messages you on Telegram with what
to do. It can also research new airdrop channels via Telegram search.

## Files
- `bot.py` — the bot
- `requirements.txt` — Python deps
- `.env.example` — all config (copy to `.env` locally)
- `../Dockerfile` — root image used by Koyeb

## 1. Generate a Pyrogram session string
```bash
pip install pyrogram tgcrypto
python - <<'PY'
from pyrogram import Client
c = Client("gen", api_id=API_ID, api_hash="API_HASH", in_memory=True)
with c:
    print("SESSION:", c.export_session_string())
PY
```

## 2. Get your Base44 API values
- App ID — editor URL: …/apps/<APP_ID>/editor
- API key — Workspace Settings → Preferences → API Key
- API base — Dashboard → API → Python snippet (default https://base44.app).

## 3. Get a notification bot (for manual fallback)
- Create a bot via @BotFather → copy TG_BOT_TOKEN.
- Get your own chat id (send a message to the bot, then use getUpdates) → NOTIFY_CHAT_ID.
- The bot sends you YES alerts AND "manual action needed" messages here.

## 4. Deploy on Koyeb
```
TG_API_ID, TG_API_HASH, TG_SESSION
TG_BOT_TOKEN, NOTIFY_CHAT_ID
GROQ_API_KEY, GROQ_MODEL
BASE44_APP_ID, BASE44_API_KEY, BASE44_API_BASE
DEFAULT_AUTONOMY, RESEARCH_INTERVAL, RESEARCH_MAX_NEW, DAILY_INTERVAL
```
