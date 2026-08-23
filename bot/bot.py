"""
AirdropHunter AI — 24/7 Pyrogram monitoring bot.

- Reads active MonitoredGroup list + non-secret BotSetting config from the
  Base44 REST API at runtime (no backend function, no integration-credit use).
- Analyzes every new message in those groups with Groq (free tier).
- Per the dashboard's autonomy mode, either auto-acts (/start ...), waits for
  approval, or only logs — and writes an AirdropSignal + ActionLog record back
  to Base44 via the REST API.
- Optionally sends a YES-signal alert via a Bot API token.

Secrets live ONLY in environment variables (Koyeb), never in the database.
"""

import os
import sys
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = os.getenv("TG_SESSION", "")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

BASE44_APP_ID = os.getenv("BASE44_APP_ID", "")
BASE44_API_KEY = os.getenv("BASE44_API_KEY", "")
BASE44_API_BASE = (os.getenv("BASE44_API_BASE", "https://www.base44.com") or "").rstrip("/")

CONFIG_REFRESH_SECONDS = int(os.getenv("CONFIG_REFRESH_SECONDS", "300"))
DEFAULT_AUTONOMY = os.getenv("DEFAULT_AUTONOMY", "human_in_loop")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("airdrophunter")


def _require(name, value):
    if not value:
        log.error("Missing required env var: %s", name)
        sys.exit(1)


for _n, _v in [
    ("TG_API_ID", TG_API_ID),
    ("TG_API_HASH", TG_API_HASH),
    ("TG_SESSION", TG_SESSION),
    ("GROQ_API_KEY", GROQ_API_KEY),
    ("BASE44_APP_ID", BASE44_APP_ID),
    ("BASE44_API_KEY", BASE44_API_KEY),
]:
    _require(_n, _v)


# --------------------------------------------------------------------------- #
# Base44 REST client
# --------------------------------------------------------------------------- #
class Base44Client:
    def __init__(self, base, app_id, api_key):
        self.base = base
        self.app_id = app_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _url(self, entity, extra=""):
        return f"{self.base}/api/apps/{self.app_id}/entities/{entity}" + extra

    async def _req(self, client, method, url, **kw):
        resp = await client.request(method, url, headers=self.headers, timeout=30, **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"Base44 {method} {url} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def list(self, client, entity):
        return await self._req(client, "GET", self._url(entity))

    async def create(self, client, entity, payload):
        return await self._req(client, "POST", self._url(entity), json=payload)

    async def get_settings(self, client):
        data = await self._req(client, "GET", self._url("BotSetting"))
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return {s.get("key"): (s.get("value") or "") for s in (items or [])}


b44 = Base44Client(BASE44_API_BASE, BASE44_APP_ID, BASE44_API_KEY)


# --------------------------------------------------------------------------- #
# AI brain (mirrors src/lib/aiBrain.js)
# --------------------------------------------------------------------------- #
BRAIN_PROMPT = """You are "AirdropHunter AI", an expert crypto airdrop analyst.
For each Telegram message decide whether it announces a REAL, high-potential
airdrop worth participating in.

Rubric (0-100 each):
- legitimacy   : verified project, tokenomics, official channels
- value        : expected reward vs effort
- urgency      : deadline / listing pressure
- accessibility: how easy to join (bot /start, faucet, task)
- safety       : scam risk, wallet-drain risk

Decision rules:
- YES  -> confident this is worth acting on
- NO   -> spam, scam, old, irrelevant
- UNSURE -> promising but missing critical info

Return ONLY JSON with keys: decision, confidence (0-100 int), reasoning (<=200 chars),
suggested_action (exact text to send, e.g. "/start" or "/tap", or "" if none)."""


async def analyze_with_groq(client, message_text, source_group, custom_prompt=""):
    user_prompt = (
        f"Source group: {source_group}\n\nMessage:\n{message_text}\n\n"
        "Analyze and return the JSON object only."
    )
    system = (custom_prompt or BRAIN_PROMPT)
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    resp = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=40,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:300]}")
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except Exception:
        return {"decision": "UNSURE", "confidence": 0, "reasoning": content[:200], "suggested_action": ""}
    return {
        "decision": (parsed.get("decision") or "UNSURE").upper(),
        "confidence": int(parsed.get("confidence") or 0),
        "reasoning": (parsed.get("reasoning") or "")[:500],
        "suggested_action": parsed.get("suggested_action") or "",
    }


# --------------------------------------------------------------------------- #
# Telegram notification (Bot API) — optional
# --------------------------------------------------------------------------- #
async def notify(client, text):
    if not (TG_BOT_TOKEN and NOTIFY_CHAT_ID):
        return
    try:
        await client.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": NOTIFY_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception as e:
        log.warning("notify failed: %s", e)


# --------------------------------------------------------------------------- #
# Shared state refreshed from Base44
# --------------------------------------------------------------------------- #
class State:
    def __init__(self):
        self.groups = []
        self.autonomy = DEFAULT_AUTONOMY
        self.ai_prompt = ""
        self.lock = asyncio.Lock()

    async def refresh(self, http):
        try:
            data = await b44.list(http, "MonitoredGroup")
            items = data if isinstance(data, list) else data.get("items", data.get("data", []))
            active = [
                (g.get("group_username") or "").lstrip("@")
                for g in (items or [])
                if g.get("is_active", True) and g.get("group_username")
            ]
            settings = await b44.get_settings(http)
            async with self.lock:
                self.groups = active
                self.autonomy = settings.get("autonomy_mode") or DEFAULT_AUTONOMY
                self.ai_prompt = settings.get("ai_prompt") or ""
            log.info("config refreshed: %d groups, autonomy=%s", len(active), self.autonomy)
        except Exception as e:
            log.warning("config refresh failed: %s", e)

    async def snapshot(self):
        async with self.lock:
            return list(self.groups), self.autonomy, self.ai_prompt


state = State()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
async def main():
    from pyrogram import Client, filters
    from pyrogram.errors import FloodWait

    app = Client(
        "airdrophunter",
        api_id=TG_API_ID,
        api_hash=TG_API_HASH,
        session_string=TG_SESSION,
        no_updates=False,
    )

    http = httpx.AsyncClient()

    async def refresh_loop():
        while True:
            await state.refresh(http)
            await asyncio.sleep(CONFIG_REFRESH_SECONDS)

    @app.on_message(filters.group)
    async def on_message(client, message):
        chat = message.chat
        username = (chat.username or "").lstrip("@")
        if not username:
            return
        groups, autonomy, ai_prompt = await state.snapshot()
        if username not in groups:
            return

        text = (message.text or message.caption or "").strip()
        if not text or len(text) < 15:
            return

        source_group = f"@{username}"
        log.info("new msg from %s (%d chars)", source_group, len(text))

        try:
            analysis = await analyze_with_groq(http, text, source_group, ai_prompt)
        except Exception as e:
            log.warning("groq failed: %s", e)
            analysis = {"decision": "UNSURE", "confidence": 0, "reasoning": str(e)[:200], "suggested_action": ""}

        decision = analysis["decision"]
        confidence = analysis["confidence"]
        action = analysis["suggested_action"]

        acted = False
        action_status = "pending"
        action_target = ""
        details = ""

        if decision == "YES" and autonomy == "full_autonomy" and action:
            action_target = source_group
            try:
                await client.send_message(chat.id, action)
                acted = True
                action_status = "success"
                details = f"sent: {action}"
            except FloodWait as fw:
                await asyncio.sleep(fw.value)
                await client.send_message(chat.id, action)
                acted = True
                action_status = "success"
                details = f"sent after wait: {action}"
            except Exception as e:
                action_status = "failed"
                details = f"send error: {e}"

        signal_status = (
            "auto_claimed" if (decision == "YES" and acted)
            else "pending" if decision == "YES"
            else "ignored" if decision == "NO"
            else "pending"
        )
        try:
            await b44.create(http, "AirdropSignal", {
                "source_group": source_group,
                "message_text": text[:4000],
                "message_url": f"https://t.me/{username}/{message.id}",
                "detected_date": datetime.now(timezone.utc).isoformat(),
                "ai_decision": decision,
                "ai_confidence": confidence,
                "ai_reasoning": analysis["reasoning"],
                "suggested_action": action,
                "status": signal_status,
            })
        except Exception as e:
            log.warning("create signal failed: %s", e)

        if decision == "YES" or acted:
            try:
                await b44.create(http, "ActionLog", {
                    "action_type": action or "evaluate",
                    "target_chat": action_target or source_group,
                    "status": action_status,
                    "details": details or f"ai={decision} conf={confidence}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                log.warning("create log failed: %s", e)

        if decision == "YES":
            await notify(http,
                f"\U0001f680 <b>Airdrop YES</b> ({confidence}%)\n"
                f"{source_group}\n{analysis['reasoning']}\n"
                f"Action: <code>{action or '-'}</code> "
                f"[{'done' if acted else autonomy}]")

    await state.refresh(http)
    asyncio.create_task(refresh_loop())
    log.info("starting bot…")
    await app.start()
    log.info("bot started. monitoring %d groups.", len((await state.snapshot())[0]))
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await app.stop()
        await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
