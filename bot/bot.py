"""
AirdropHunter AI — 24/7 Pyrogram monitoring bot (FULL AUTO + DAILY TASKS).

Capabilities (all free, Groq-based, no human needed unless a step truly requires it):
- Monitors active groups; analyzes each message with Groq.
- FULL AUTONOMY: on a YES signal it AUTO-JOINS referenced channels/bots, sends
  the suggested command, and AUTO-TAPS inline buttons (Join/Verify/Claim/Start)
  so tasks (including the "verify" step) complete automatically — with human-like
  typing pauses so it feels like a real user.
- DAILY TASKS: when it starts an airdrop bot, it remembers it (AirdropTask entity)
  and re-sends the daily command (/tap, /daily, /claim...) every day on its own.
- MANUAL FALLBACK: if the AI decides a step requires a real human (wallet connect,
  KYC, captcha, payment, real social follow), or an auto-action fails, the bot
  sends you a Telegram message (NOTIFY_CHAT_ID) describing exactly what to do.
- RESEARCH: optionally searches Telegram for new airdrop channels (and any target
  projects you set) and adds them to the monitored list.
- Heartbeat so the dashboard can show whether the bot/Colab is connected.

Secrets live ONLY in environment variables, never in the database.
"""

import os
import re
import sys
import json
import time
import random
import asyncio
import logging
import platform
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

# Config
TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_SESSION = os.getenv("TG_SESSION", "")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")

BASE44_APP_ID = os.getenv("BASE44_APP_ID", "")
BASE44_API_KEY = os.getenv("BASE44_API_KEY", "")
BASE44_API_BASE = (os.getenv("BASE44_API_BASE", "https://base44.app") or "").rstrip("/")

CONFIG_REFRESH_SECONDS = int(os.getenv("CONFIG_REFRESH_SECONDS", "300"))
DEFAULT_AUTONOMY = os.getenv("DEFAULT_AUTONOMY", "full_autonomy")
RESEARCH_INTERVAL = int(os.getenv("RESEARCH_INTERVAL", "3600"))
RESEARCH_MAX_NEW = int(os.getenv("RESEARCH_MAX_NEW", "5"))
DAILY_INTERVAL = int(os.getenv("DAILY_INTERVAL", "21600"))  # 6h check loop

PORT = int(os.getenv("PORT", "7860"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", stream=sys.stdout)
log = logging.getLogger("airdrophunter")

for _n, _v in [("TG_API_ID", TG_API_ID), ("TG_API_HASH", TG_API_HASH), ("TG_SESSION", TG_SESSION),
               ("GROQ_API_KEY", GROQ_API_KEY), ("BASE44_APP_ID", BASE44_APP_ID), ("BASE44_API_KEY", BASE44_API_KEY)]:
    if not _v:
        log.error("Missing required env var: %s", _n)
        sys.exit(1)

USERNAME_RE = re.compile(r"(?<!\w)@([A-Za-z]\w{3,31})")
TMLINK_RE = re.compile(r"https?://t\.me/(?:joinchat/)?(\+?[A-Za-z0-9_-]+)")
TAP_LABELS = {"join", "joined", "verify", "verify join", "check", "claim", "start",
              "tap", "task", "continue", "reward", "get", "follow", "subscribe",
              "done", "confirm", "launch", "play", "farm", "collect", "next", "ok",
              "yes", "submit", "go", "begin", "earn", "claim now", "verify now"}


class Base44Client:
    def __init__(self, base, app_id, api_key):
        self.base = base
        self.app_id = app_id
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

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

    async def list_raw(self, client, entity):
        data = await self._req(client, "GET", self._url(entity))
        return data if isinstance(data, list) else data.get("items", data.get("data", []))

    async def update(self, client, entity, item_id, payload):
        url = f"{self._url(entity)}/{item_id}"
        resp = await client.request("PATCH", url, headers=self.headers, json=payload, timeout=30)
        if resp.status_code == 405:
            resp = await client.request("PUT", url, headers=self.headers, json=payload, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Base44 update -> {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception:
            return {}


b44 = Base44Client(BASE44_API_BASE, BASE44_APP_ID, BASE44_API_KEY)

BRAIN_PROMPT = """You are "AirdropHunter AI", an elite crypto airdrop analyst with deep expertise in the Telegram airdrop ecosystem (Notcoin, Hamster Kombat, Blum, Yescoin, TapSwap, Major, Dogs, X Empire and hundreds more).

For each Telegram message decide whether it announces a REAL, high-potential, low-risk airdrop that our autonomous bot should interact with — and whether the bot can fully complete the task alone.

Rubric:
- legitimacy   : real token, real bot/mini-app, reputable backing, listed on trackers
- value        : expected reward vs effort
- urgency      : deadline / listing pressure
- accessibility: how easy to join (bot /start, faucet, tap-to-earn, join channel)
- safety       : NO seed phrase / private key / upfront payment / gas fees / "send X get 2X" / drainer links / phishing

Decision rules:
- YES  (70-100): legit project + safe task + real upside. Bot SHOULD auto-interact.
- NO   (70-100): scam, phishing, payment request, fake giveaway, or worthless.
- UNSURE: mixed / unknown / unverifiable.

Return ONLY JSON (no markdown, no commentary) with keys:
- decision: "YES" | "NO" | "UNSURE"
- confidence: 0-100 int
- reasoning: <=200 chars
- suggested_action: exact bot command to send now (e.g. "/start", "/tap", "/daily", "/claim") or ""
- is_daily: true if this is a recurring daily task the bot should repeat every day
- daily_command: the command to send daily (e.g. "/tap", "/daily", "/claim") or ""
- requires_human: true if a real human MUST complete a step the bot cannot (wallet connect, KYC, captcha, payment, real social-media follow with a real account)
- human_action: short description of what the human must do, or ""

Be decisive but rigorous. Prefer missing a mediocre airdrop over auto-claiming a scam."""


def _to_bool(v, default=False):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "on", "y")


async def analyze_with_groq(client, message_text, source_group, custom_prompt="", targets=None):
    user_prompt = f"Source group: {source_group}\n\nMessage:\n{message_text}\n\n"
    if targets:
        user_prompt += f"Projects of special interest (prioritize these): {', '.join(targets)}\n\n"
    user_prompt += "Analyze and return the JSON object only."
    system = custom_prompt or BRAIN_PROMPT
    payload = {"model": GROQ_MODEL, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
        "response_format": {"type": "json_object"}, "temperature": 0.2}
    resp = await client.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=40)
    if resp.status_code >= 400:
        raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:300]}")
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        p = json.loads(content)
    except Exception:
        return {"decision": "UNSURE", "confidence": 0, "reasoning": content[:200], "suggested_action": "",
                "is_daily": False, "daily_command": "", "requires_human": False, "human_action": ""}
    return {"decision": (p.get("decision") or "UNSURE").upper(),
            "confidence": int(p.get("confidence") or 0),
            "reasoning": (p.get("reasoning") or "")[:500],
            "suggested_action": p.get("suggested_action") or "",
            "is_daily": _to_bool(p.get("is_daily"), False),
            "daily_command": (p.get("daily_command") or "").strip(),
            "requires_human": _to_bool(p.get("requires_human"), False),
            "human_action": (p.get("human_action") or "").strip()}


def _truthy(v, default=True):
    if v is None or v == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


async def human_pause(client, chat_id):
    try:
        await client.send_chat_action(chat_id, "typing")
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1.0, 3.0))


async def perform_action(client, source_chat_id, analysis, text, auto_join):
    action = (analysis.get("suggested_action") or "").strip()
    combined = f"{text}\n{action}"
    usernames = list(dict.fromkeys(USERNAME_RE.findall(combined)))
    links = TMLINK_RE.findall(combined)
    bot_targets = [u for u in usernames if u.lower().endswith("bot")]
    channel_targets = [u for u in usernames if not u.lower().endswith("bot")]
    out = []
    if auto_join:
        for u in channel_targets:
            try:
                await client.join_chat(u)
                out.append((True, f"joined @{u}"))
            except Exception as e:
                out.append((False, f"join @{u} failed: {e}"))
        for l in links:
            link = f"https://t.me/+{l[1:]}" if l.startswith("+") else f"https://t.me/{l}"
            try:
                await client.join_chat(link)
                out.append((True, f"joined link {l}"))
            except Exception as e:
                out.append((False, f"link {l} failed: {e}"))
    if action and action.strip() not in ("do_not_interact", "manual_review", "join_channel", "connect_wallet"):
        target = bot_targets[0] if bot_targets else None
        if target:
            try:
                await human_pause(client, target)
                await client.send_message(target, action)
                out.append((True, f"sent {action} @{target}"))
            except Exception as e:
                out.append((False, f"send @{target} failed: {e}"))
        else:
            try:
                await human_pause(client, source_chat_id)
                await client.send_message(source_chat_id, action)
                out.append((True, f"sent {action}"))
            except Exception as e:
                out.append((False, f"send failed: {e}"))
    return out


async def tap_inline_buttons(client, message):
    rm = getattr(message, "reply_markup", None)
    rows = getattr(rm, "inline_keyboard", None) if rm else None
    if not rows:
        return []
    out = []
    for y, row in enumerate(rows):
        for x, btn in enumerate(row):
            label = (getattr(btn, "text", "") or "").strip().lower()
            if not label or not any(k in label for k in TAP_LABELS):
                continue
            url = getattr(btn, "url", None)
            cb = getattr(btn, "callback_data", None)
            if url:
                m = re.search(r"t\.me/(?:joinchat/)?(\+?[A-Za-z0-9_-]+)", url)
                if m:
                    tgt = m.group(1)
                    link = f"https://t.me/+{tgt[1:]}" if tgt.startswith("+") else f"https://t.me/{tgt}"
                    try:
                        await client.join_chat(link)
                        out.append((True, f"btn-join:{tgt}"))
                    except Exception as e:
                        out.append((False, f"btn-join {tgt} failed: {e}"))
            elif cb:
                try:
                    await message.click(x, y)
                    out.append((True, f"tap:{btn.text}"))
                except Exception as e:
                    out.append((False, f"tap {btn.text} failed: {e}"))
    return out


async def ensure_task(http, bot_username, daily_command, source_group, requires_human, manual_note):
    if not bot_username:
        return
    bot_username = bot_username.lstrip("@")
    tasks = await b44.list_raw(http, "AirdropTask")
    existing = next((t for t in tasks if (t.get("bot_username") or "").lstrip("@") == bot_username), None)
    payload = {"daily_command": daily_command or "/tap", "source_group": source_group,
               "requires_human": requires_human, "manual_note": manual_note}
    if existing:
        await b44.update(http, "AirdropTask", existing["id"], payload)
    else:
        payload.update({"bot_username": bot_username, "is_active": True})
        await b44.create(http, "AirdropTask", payload)


async def notify(client, text):
    if not (TG_BOT_TOKEN and NOTIFY_CHAT_ID):
        return
    try:
        await client.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": NOTIFY_CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        log.warning("notify failed: %s", e)


async def keepalive_server():
    async def handle(reader, writer):
        try:
            await reader.read(4096)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    log.info("keepalive http server on :%d", PORT)
    async with server:
        await server.serve_forever()


async def heartbeat_loop(http):
    hb_id = None
    st_id = None
    start = time.time()
    while True:
        try:
            raw = await b44.list_raw(http, "BotSetting")
            for s in raw or []:
                k = s.get("key")
                if k == "bot_heartbeat":
                    hb_id = s.get("id")
                elif k == "bot_status":
                    st_id = s.get("id")
            now_iso = datetime.now(timezone.utc).isoformat()
            host = os.getenv("HOSTNAME") or platform.node() or "colab"
            st_val = json.dumps({"host": host, "model": GROQ_MODEL,
                                 "uptime_min": int((time.time() - start) / 60), "last_seen": now_iso})
            if hb_id:
                await b44.update(http, "BotSetting", hb_id, {"value": now_iso})
            else:
                await b44.create(http, "BotSetting", {"key": "bot_heartbeat", "value": now_iso, "category": "general"})
            if st_id:
                await b44.update(http, "BotSetting", st_id, {"value": st_val})
            else:
                await b44.create(http, "BotSetting", {"key": "bot_status", "value": st_val, "category": "general"})
            log.info("heartbeat sent @ %s", now_iso)
        except Exception as e:
            log.warning("heartbeat failed: %s", e)
        await asyncio.sleep(60)


class State:
    def __init__(self):
        self.groups = []
        self.autonomy = DEFAULT_AUTONOMY
        self.ai_prompt = ""
        self.auto_join = True
        self.auto_tap = True
        self.auto_research = False
        self.targets = []
        self.lock = asyncio.Lock()

    async def refresh(self, http):
        try:
            data = await b44.list(http, "MonitoredGroup")
            items = data if isinstance(data, list) else data.get("items", data.get("data", []))
            active = [(g.get("group_username") or "").lstrip("@") for g in (items or [])
                      if g.get("is_active", True) and g.get("group_username")]
            settings = await b44.get_settings(http)
            tval = settings.get("target_airdrops") or ""
            targets = [t.strip() for t in tval.split(",") if t.strip()]
            async with self.lock:
                self.groups = active
                self.autonomy = settings.get("autonomy_mode") or DEFAULT_AUTONOMY
                self.ai_prompt = settings.get("ai_prompt") or ""
                self.auto_join = _truthy(settings.get("auto_join"), True)
                self.auto_tap = _truthy(settings.get("auto_tap"), True)
                self.auto_research = _truthy(settings.get("auto_research"), False)
                self.targets = targets
            log.info("config refreshed: %d groups, autonomy=%s, join=%s tap=%s research=%s targets=%d",
                     len(active), self.autonomy, self.auto_join, self.auto_tap, self.auto_research, len(targets))
        except Exception as e:
            log.warning("config refresh failed: %s", e)

    async def snapshot(self):
        async with self.lock:
            return (list(self.groups), self.autonomy, self.ai_prompt,
                    self.auto_join, self.auto_tap, self.auto_research, list(self.targets))


state = State()


async def daily_task_loop(http, client):
    while True:
        await asyncio.sleep(DAILY_INTERVAL)
        try:
            _, autonomy, _, _, _, _, _ = await state.snapshot()
            if autonomy != "full_autonomy":
                continue
            tasks = await b44.list_raw(http, "AirdropTask")
            today = datetime.now(timezone.utc).date()
            for t in tasks or []:
                if not t.get("is_active", True):
                    continue
                bot = (t.get("bot_username") or "").lstrip("@")
                if not bot or not t.get("id"):
                    continue
                last = t.get("last_performed")
                if last:
                    try:
                        ld = datetime.fromisoformat(last.replace("Z", "")).date()
                        if ld == today:
                            continue
                    except Exception:
                        pass
                cmd = t.get("daily_command") or "/tap"
                now_iso = datetime.now(timezone.utc).isoformat()
                if t.get("requires_human"):
                    note = t.get("manual_note") or "manual action needed"
                    await notify(http, f"👤 <b>Manual action needed (daily reminder)</b>\n@{bot}\n{note}\nComplete it so the bot can keep farming.")
                    await b44.update(http, "AirdropTask", t["id"], {"last_performed": now_iso})
                    try:
                        await b44.create(http, "ActionLog", {"action_type": "manual_needed",
                            "target_chat": f"@{bot}", "status": "pending",
                            "details": note[:500], "timestamp": now_iso})
                    except Exception:
                        pass
                    continue
                try:
                    await human_pause(client, bot)
                    await client.send_message(bot, cmd)
                    await b44.update(http, "AirdropTask", t["id"], {"last_performed": now_iso})
                    await b44.create(http, "ActionLog", {"action_type": "daily_task",
                        "target_chat": f"@{bot}", "status": "success",
                        "details": f"sent {cmd}", "timestamp": now_iso})
                    log.info("daily task done @%s: %s", bot, cmd)
                except Exception as e:
                    await notify(http, f"⚠️ Daily task failed for @{bot}: {e}\nPlease check manually.")
                    try:
                        await b44.create(http, "ActionLog", {"action_type": "daily_task",
                            "target_chat": f"@{bot}", "status": "failed",
                            "details":
