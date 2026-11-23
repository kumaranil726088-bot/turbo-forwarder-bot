"""
👑 PRO TURBO TRANSFER PIPELINE v4.0 — ULTRA POWER 👑
====================================================
- TXT file / batch / auto-detect se full auto-forwarding
- /forward (private/protected) + /publicforward (public direct copy)
- ✨ FORWARD SETTINGS PANEL — Promo / Caption / Rename / Hyperlink /
  Thumbnail / Destination Topic / Sender + SAVE ALL & START
- TURBO engine: instant file_id copy → warna download + re-upload
- Parallel workers, LIVE card (cooldown display ke saath), Instant Stop & Clear
- 🔄 RESUME system — restart pe pending task wahi se continue
- 🔴 LIVE watch 24/7 (multi-route) + ⚡ auto-detect + 🔥 user session
- /id, /setchannel, /grouptransfer, /bottransfer, message-type filters
"""

import os
import re
import html
import json
import time
import uuid
import asyncio
import logging
import sqlite3
import threading
import shutil
from collections import deque

import aiohttp
from aiohttp import web
from telethon import TelegramClient, events, Button
from telethon.extensions import html as tl_html
from telethon.sessions import StringSession
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import (
    PeerChannel,
    MessageMediaWebPage,
    BotCommand,
    BotCommandScopeDefault,
)
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    ChatForwardsRestrictedError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeEmptyError,
)

# ------------------------------------------------------------------ #
#  CONFIG                                                             #
# ------------------------------------------------------------------ #
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "botdata.db")
DL_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")
START_TIME = time.time()

sudo_raw = os.environ.get("SUDO_USERS", "")
SUDO_IDS = set()
for part in sudo_raw.replace(" ", "").split(","):
    if part.strip().isdigit():
        SUDO_IDS.add(int(part))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("turbobot")

if not (API_ID and API_HASH and BOT_TOKEN and OWNER_ID):
    log.error("Env vars missing! Chahiye: API_ID, API_HASH, BOT_TOKEN, OWNER_ID")

try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

client = TelegramClient(StringSession(), API_ID, API_HASH)  # BOT client (in-memory session)
user_client = None                                          # USER session (fetch power)
USER_ACTIVE = False

os.makedirs(DL_DIR, exist_ok=True)

# ------------------------------------------------------------------ #
#  DATABASE                                                           #
# ------------------------------------------------------------------ #
_db_lock = threading.Lock()

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    return conn

def get_setting(key, default=None):
    with _db_lock:
        conn = _db()
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        conn.close()
    return row[0] if row else default

def set_setting(key, value):
    with _db_lock:
        conn = _db()
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        conn.commit()
        conn.close()

def _json(key, default):
    raw = get_setting(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default

def get_replacements():
    return _json("replacements", [])

def get_watch_list():
    """Multi-route watch: [{src, target}, ...]. Purana format (str list) bhi handle."""
    raw = _json("watch", [])
    out = []
    for item in raw:
        if isinstance(item, str):
            out.append({"src": item, "target": get_setting("target", "")})
        elif isinstance(item, dict) and item.get("src"):
            out.append({"src": item["src"], "target": item.get("target") or get_setting("target", "")})
    return out

def save_watch_list(lst):
    set_setting("watch", json.dumps(lst))

def get_lastids():
    return _json("lastids", {})

def save_lastids(d):
    set_setting("lastids", json.dumps(d))

def get_type_filters():
    """Enabled message types. Default: sab on."""
    all_types = ["text", "document", "video", "photo", "audio", "voice", "animation", "sticker", "poll"]
    enabled = _json("type_filters", None)
    if enabled is None:
        return set(all_types)
    return set(enabled)

def set_type_filters(enabled):
    set_setting("type_filters", json.dumps(sorted(enabled)))

# ------------------------------------------------------------------ #
#  LINK PARSING                                                       #
# ------------------------------------------------------------------ #
LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/(c/)?([A-Za-z0-9_]{3,64})/(\d+)",
    re.IGNORECASE,
)
TOPIC_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/c/(\d+)/(\d+)/(\d+)",
    re.IGNORECASE,
)
PLAIN_RE = re.compile(r"^@?([A-Za-z0-9_]{3,64})\s+(\d+)$", re.IGNORECASE)

def parse_post_link(line):
    """Return (chat_ref, msg_id) or None. chat_ref = '-100id' ya 'username'."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = TOPIC_LINK_RE.search(line)
    if m:
        return (f"-100{m.group(1)}", int(m.group(3)))
    m = LINK_RE.search(line)
    if m:
        chat, msg_id = m.group(2), int(m.group(3))
        return (f"-100{chat}", msg_id) if m.group(1) else (chat, msg_id)
    m = PLAIN_RE.match(line)
    if m:
        return (m.group(1), int(m.group(2)))
    return None

def parse_all_links(text):
    """Message me jitne bhi t.me post links hain, sab nikalo."""
    out = []
    for m in LINK_RE.finditer(text):
        chat, msg_id = m.group(2), int(m.group(3))
        out.append((f"-100{chat}" if m.group(1) else chat, msg_id))
    return out

def parse_channel_ref(text):
    t = text.strip()
    m = re.search(r"t\.me/(c/)?([A-Za-z0-9_]{3,64})/?$", t, re.IGNORECASE)
    if m:
        return f"-100{m.group(2)}" if m.group(1) else m.group(2)
    t = t.lstrip("@")
    if re.fullmatch(r"[A-Za-z0-9_]{3,64}", t):
        return t
    if re.fullmatch(r"-100\d+", t):
        return t
    return None

def parse_links(text):
    links = []
    for line in text.splitlines():
        parsed = parse_post_link(line)
        if parsed:
            links.append(parsed)
    return links

def apply_replacements(text):
    for old, new in get_replacements():
        if old:
            text = text.replace(old, new)
    return text

def apply_hyperlink(text, hl):
    """Hyperlink: 'Label|https://url' ya 'all|https://url'."""
    if not hl or "|" not in hl or not text:
        return text
    label, url = hl.split("|", 1)
    label, url = label.strip(), url.strip()
    if not url.startswith("http"):
        return text
    if label.lower() == "all":
        return re.sub(r"https?://\S+", lambda m: f'<a href="{url}">{m.group(0)}</a>', text)
    return text.replace(label, f'<a href="{url}">{label}</a>')

def build_text(original, caption_tpl, index=None, title=None, cfg=None):
    """Caption template build karo. Placeholders: {original} {caption} {index} {title}."""
    cfg = cfg or {}
    original = apply_replacements(original or "")
    tpl = caption_tpl or ""
    if tpl:
        tpl = apply_replacements(tpl)
        out = tpl.replace("{original}", original).replace("{caption}", original)
        if index is not None:
            out = out.replace("{index}", f"{index:03d}")
        if title:
            out = out.replace("{title}", title)
        text = out.strip()
    else:
        text = original
    # Hyperlink apply
    text = apply_hyperlink(text, cfg.get("hyperlink", ""))
    # Promo append
    promo = cfg.get("promo", "")
    if promo:
        text = (text + f"\n\n👉 {promo}").strip()
    return text

def resolve_for(cl, ref):
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        s = ref.strip()
        if s.startswith("-100") and s[4:].isdigit():
            return PeerChannel(int(s[4:]))
        if re.fullmatch(r"-?\d+", s):
            return int(s)
    return ref

def get_fetcher():
    return user_client if USER_ACTIVE else client

def msg_type(m):
    """Message ka type string (filter ke liye)."""
    try:
        if getattr(m, "sticker", None):
            return "sticker"
        if getattr(m, "poll", None):
            return "poll"
        if getattr(m, "voice", None):
            return "voice"
        if getattr(m, "video_note", None):
            return "video"
        if getattr(m, "gif", None):
            return "animation"
        if getattr(m, "video", None):
            return "video"
        if getattr(m, "audio", None):
            return "audio"
        if getattr(m, "photo", None):
            return "photo"
        if getattr(m, "document", None):
            return "document"
    except Exception:
        pass
    return "text"

# ------------------------------------------------------------------ #
#  FORMAT HELPERS                                                     #
# ------------------------------------------------------------------ #
def fmt_size(n):
    if not n:
        return "0.00 MB"
    return f"{n / (1024 * 1024):.2f} MB"

def fmt_eta(sec):
    sec = max(int(sec), 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

# ------------------------------------------------------------------ #
#  JOB STATE + LIVE CARD                                              #
# ------------------------------------------------------------------ #
CURRENT = {
    "active": False,
    "cancel": False,
    "done": 0,
    "total": 0,
    "ok": 0,
    "fail": 0,
    "skip": 0,
    "log": "Starting Turbo Transfer...",
    "label": "",
    "target": "",
    "phase": "🔗 Connecting...",
    "dl": (0, 0),
    "ul": (0, 0),
    "samples": None,
    "progress_msg": None,
    "started": 0,
    "tasks": [],
    "resumed": False,
    "moved_total": 0,
    "idx_offset": 0,
    "pending_links": [],
    "cooldown_until": 0,
    "cfg": {},
}

def reset_job():
    CURRENT.update(
        active=False, cancel=False, done=0, total=0, ok=0, fail=0, skip=0,
        log="Starting Turbo Transfer...", label="", target="",
        phase="🔗 Connecting...", dl=(0, 0), ul=(0, 0), samples=None,
        progress_msg=None, started=0, tasks=[], resumed=False,
        moved_total=0, idx_offset=0, pending_links=[], cooldown_until=0, cfg={},
    )

def is_owner(uid):
    return uid == OWNER_ID or uid in SUDO_IDS

def owner_filter(func):
    async def wrapper(event):
        if not is_owner(event.sender_id):
            await event.reply("⛔ Sirf owner ke liye hai ye command!")
            return
        try:
            await func(event)
        except Exception as e:
            log.exception("Handler error: %s", e)
            try:
                await event.reply(f"⚠️ Error: <code>{e}</code>", parse_mode="html")
            except Exception:
                pass
    return wrapper

def job_bytes():
    return CURRENT["dl"][0] + CURRENT["ul"][0]

def render_card(final=False):
    if final:
        head = "🛑 <b>STOPPED &amp; CLEARED</b>" if CURRENT["cancel"] else "🏁 <b>JOB COMPLETE!</b>"
    else:
        head = "👑 <b>PRO TURBO TRANSFER PIPELINE</b> 👑"
    abs_done = CURRENT["idx_offset"] + CURRENT["done"]
    lines = [
        head,
        "<i>━━━━━━━━━━━━━━━━━━━━━━</i>",
        f"📌 <b>Source:</b> {CURRENT['label']}",
        f"🎯 <b>Destination:</b> {CURRENT['target']}",
        f"📊 <b>Progress:</b> {abs_done}/{CURRENT['total']}",
    ]
    if not final:
        d_cur, d_tot = CURRENT["dl"]
        u_cur, u_tot = CURRENT["ul"]
        if CURRENT["phase"].startswith("📥") and d_tot:
            mb_line = f"📥 <b>Downloading:</b> {d_cur * 100 / max(d_tot, 1):.1f}% ({fmt_size(d_cur)}/{fmt_size(d_tot)})"
        elif CURRENT["phase"].startswith("📤") and u_tot:
            mb_line = f"📤 <b>Uploading:</b> {u_cur * 100 / max(u_tot, 1):.1f}% ({fmt_size(u_cur)}/{fmt_size(u_tot)})"
        elif CURRENT["phase"].startswith("✨"):
            mb_line = "✨ <b>Instant Copy</b> (file_id — superfast)"
        else:
            mb_line = f"🔗 <b>Stage:</b> {CURRENT['phase'][2:].strip()}"
        lines.append(mb_line)

        # speed over last samples
        speed = 0.0
        if CURRENT["samples"]:
            s = list(CURRENT["samples"])
            if len(s) >= 2:
                (t0, b0), (t1, b1) = s[0], s[-1]
                dt = t1 - t0
                if dt > 0.5:
                    speed = (b1 - b0) / dt
        cooldown_left = max(int(CURRENT.get("cooldown_until", 0) - time.time()), 0)
        if cooldown_left > 0:
            lines.append(f"⚡ <b>Speed:</b> Paused by Telegram")
            lines.append(f"🕐 <b>Telegram cooldown:</b> {cooldown_left}s • Auto-resume enabled")
        elif speed > 0:
            lines.append(f"⚡ <b>Speed:</b> {speed / (1024 * 1024):.2f} MB/s")

        lines.append(
            f"✅ <b>Transferred:</b> {CURRENT['ok']} | ⏭ <b>Skipped:</b> {CURRENT['skip']}"
        )
        if CURRENT["fail"]:
            lines.append(f"❌ <b>Failed:</b> {CURRENT['fail']}")

        # ETA
        elapsed = max(time.time() - CURRENT["started"], 0) if CURRENT["started"] else 0
        done = max(CURRENT["done"], 1)
        avg = elapsed / done
        eta = avg * max(CURRENT["total"] - abs_done, 0) + 8
        lines.append(f"⏱ <b>Time Remaining:</b> ~ {fmt_eta(eta)}")

    lines.append(f"📋 <b>Status Log:</b> {CURRENT['log'][:120]}")
    return "\n".join(lines)

STOP_KB = [[Button.inline("⏹ Instant Stop & Clear", b"stopjob")]]

async def card_updater():
    """Har 5s me live card edit karo (flood-safe throttle) + speed sampling."""
    last_text = ""
    while True:
        await asyncio.sleep(5)
        if not CURRENT["active"]:
            return
        if CURRENT["samples"] is not None:
            CURRENT["samples"].append((time.time(), CURRENT["moved_total"] + job_bytes()))
        try:
            text = render_card()
            if text != last_text:
                last_text = text
                await CURRENT["progress_msg"].edit(
                    text, parse_mode="html", buttons=STOP_KB
                )
        except Exception:
            pass

# ------------------------------------------------------------------ #
#  PENDING JOB PERSISTENCE (RESUME SYSTEM)                            #
# ------------------------------------------------------------------ #
def save_pending():
    """Har item ke baad state save — restart pe wahi se resume."""
    set_setting("pending_job", json.dumps({
        "label": CURRENT["label"],
        "target": CURRENT["target"],
        "mode": CURRENT.get("job_mode", "turbo"),
        "caption": CURRENT.get("job_caption", ""),
        "cfg": CURRENT.get("cfg", {}),
        "links": CURRENT.get("pending_links", []),
        "idx": CURRENT["idx_offset"] + CURRENT["done"],
        "ok": CURRENT["ok"],
        "fail": CURRENT["fail"],
        "skip": CURRENT["skip"],
        "log": CURRENT["log"],
        "ts": time.time(),
    }))

def clear_pending():
    set_setting("pending_job", "")

def load_pending():
    raw = get_setting("pending_job", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

# ------------------------------------------------------------------ #
#  SAFE CALL                                                          #
# ------------------------------------------------------------------ #
async def safe_call(fn, *args, **kwargs):
    while True:
        try:
            return await fn(*args, **kwargs)
        except FloodWaitError as e:
            wait_s = min(e.seconds + 1, 600)
            CURRENT["cooldown_until"] = time.time() + wait_s
            CURRENT["log"] = f"Telegram cooldown: {e.seconds}s — auto-resume enabled"
            log.warning("FloodWait %ss", e.seconds)
            await asyncio.sleep(wait_s)

# ------------------------------------------------------------------ #
#  TURBO TRANSFER — ek item ka pura pipeline                          #
# ------------------------------------------------------------------ #
def make_progress_cb(kind):
    def cb(cur, tot):
        if tot:
            CURRENT[kind] = (cur, tot)
    return cb

async def fetch_msg_group(fetcher, chat, msg_id):
    """Single msg ya album (grouped) fetch karo. Return list of msgs."""
    msg = await safe_call(fetcher.get_messages, chat, ids=msg_id)
    if not msg:
        return []
    if msg.grouped_id:
        window = list(range(msg_id, msg_id + 10))
        group = await safe_call(fetcher.get_messages, chat, ids=window)
        parts = [
            m for m in group
            if m and m.grouped_id == msg.grouped_id
            and m.media and not isinstance(m.media, MessageMediaWebPage)
        ]
        if parts:
            return sorted(parts, key=lambda m: m.id)
    return [msg]

def is_mediaable(msg):
    return bool(msg.media) and not isinstance(msg.media, MessageMediaWebPage)

async def get_thumb_path(url):
    """Thumbnail URL download karo → local path. Fail ho to None."""
    if not url or not url.startswith("http"):
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return None
                data = await r.read()
        path = os.path.join(DL_DIR, "thumb.jpg")
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        log.info("Thumbnail download fail: %s", e)
        return None

async def _send_html_safe(sender_fn, dest_ent, text, **kw):
    """HTML parse_mode ke saath send karo; agar malformed tag ho to plain fallback."""
    try:
        return await safe_call(sender_fn, dest_ent, text, parse_mode="html", **kw)
    except Exception:
        return await safe_call(sender_fn, dest_ent, text, **kw)

async def instant_copy(dest, msgs, text, cfg):
    """file_id server-side copy — no download/upload, INSTANT."""
    dest_ent = resolve_for(client, dest)
    reply_to = cfg.get("topic") or None
    files = [m.media for m in msgs if is_mediaable(m)]
    thumb = None
    if cfg.get("thumb"):
        thumb = await get_thumb_path(cfg["thumb"])
    if len(files) > 1:
        await _send_html_safe(client.send_message, dest_ent, text or "", file=files, reply_to=reply_to)
    elif files:
        kw = {"file": files[0], "reply_to": reply_to}
        if thumb:
            kw["thumb"] = thumb
        await _send_html_safe(client.send_message, dest_ent, text or "", **kw)
    else:
        await _send_html_safe(client.send_message, dest_ent, text or "", reply_to=reply_to)

def _rename_file_if_needed(path, cfg):
    """Filename par rename/replace rules lagao."""
    d, base = os.path.split(path)
    new_base = apply_replacements(base)
    rn = (cfg or {}).get("rename", "")
    if rn and "|" in rn:
        old_s, new_s = rn.split("|", 1)
        if old_s.strip():
            new_base = new_base.replace(old_s.strip(), new_s.strip())
    # Sanitize path chars
    new_base = new_base.replace("/", "_").replace("\\", "_").strip() or base
    if new_base != base:
        new_path = os.path.join(d, new_base)
        try:
            os.rename(path, new_path)
            return new_path
        except Exception:
            pass
    return path

async def turbo_download(fetcher, msgs, item_dir, cfg=None):
    """Album/singles per-item temp folder me download karo."""
    os.makedirs(item_dir, exist_ok=True)
    paths = []
    for m in msgs:
        if not is_mediaable(m):
            continue
        CURRENT["phase"] = "📥 Downloading"
        p = await safe_call(
            fetcher.download_media, m, file=item_dir,
            progress_callback=make_progress_cb("dl"),
        )
        if p:
            paths.append(_rename_file_if_needed(p, cfg))
    return paths

async def turbo_upload(paths, text, dest, cfg):
    CURRENT["phase"] = "📤 Uploading"
    dest_ent = resolve_for(client, dest)
    reply_to = cfg.get("topic") or None
    thumb = None
    if cfg.get("thumb"):
        thumb = await get_thumb_path(cfg["thumb"])
    if len(paths) > 1:
        try:
            await safe_call(
                client.send_file, dest_ent, paths,
                caption=text or "", parse_mode="html",
                progress_callback=make_progress_cb("ul"),
                reply_to=reply_to,
            )
        except Exception:
            await safe_call(
                client.send_file, dest_ent, paths,
                caption=text or "",
                progress_callback=make_progress_cb("ul"),
                reply_to=reply_to,
            )
    elif paths:
        kw = {"caption": text or "", "progress_callback": make_progress_cb("ul"), "reply_to": reply_to}
        if thumb:
            kw["thumb"] = thumb
        try:
            await safe_call(client.send_file, dest_ent, paths[0], parse_mode="html", **kw)
        except Exception:
            await safe_call(client.send_file, dest_ent, paths[0], **kw)
    else:
        await _send_html_safe(client.send_message, dest_ent, text or "", reply_to=reply_to)

async def transfer_item(dest, chat_ref, msg_id, cfg=None, index=None):
    """
    Ek post ko dest tak pahunchao.
    Returns (status, reason): 'ok' | 'skip' | 'fail'
    Strategy: public/bot instant file_id copy → fail/restricted pe turbo download+upload.
    """
    cfg = cfg or {}
    fetcher = get_fetcher()
    dest_ent = resolve_for(client, dest)
    CURRENT["phase"] = "🔗 Connecting..."
    CURRENT["dl"] = (0, 0)
    CURRENT["ul"] = (0, 0)

    # Public channel ya /publicforward mode me pehle bot client se try karo (instant server-side copy)
    bot_msgs = None
    is_public_ref = isinstance(chat_ref, str) and not chat_ref.startswith("-100") and not chat_ref.lstrip("-").isdigit()
    if fetcher is not client and (cfg.get("mode") == "copy" or is_public_ref):
        try:
            b_chat = await safe_call(client.get_entity, resolve_for(client, chat_ref))
            bot_msgs = await fetch_msg_group(client, b_chat, msg_id)
        except Exception:
            bot_msgs = None

    if bot_msgs:
        msgs = bot_msgs
        used_bot = True
    else:
        used_bot = (fetcher is client)
        try:
            chat = await safe_call(fetcher.get_entity, resolve_for(fetcher, chat_ref))
            msgs = await fetch_msg_group(fetcher, chat, msg_id)
        except (ChannelPrivateError, ValueError):
            return "fail", f"Chat {chat_ref} accessible nahi (access chahiye)"
    if not msgs:
        return "fail", f"Message #{msg_id} not found"

    first = msgs[0]
    # Message-type filter
    enabled = get_type_filters()
    if msg_type(first) not in enabled:
        return "skip", f"Type filtered: {msg_type(first)}"

    # Title (filename ya pehli line) — metadata caption ke liye
    title = ""
    if getattr(first, "file", None) and getattr(first.file, "name", None):
        title = apply_replacements(first.file.name)
    elif first.message:
        title = first.message.splitlines()[0][:80]

    # Original formatting (bold/italic/links) preserve in HTML
    raw_msg = first.message or ""
    if raw_msg and getattr(first, "entities", None):
        try:
            formatted_orig = tl_html.unparse(raw_msg, first.entities)
        except Exception:
            formatted_orig = html.escape(raw_msg)
    else:
        formatted_orig = html.escape(raw_msg) if raw_msg else ""

    caption_tpl = cfg.get("caption") or get_setting("caption", "")
    text = build_text(formatted_orig, caption_tpl, index=index, title=html.escape(title), cfg=cfg)

    # text-only post → filter setting
    if not any(is_mediaable(m) for m in msgs):
        if get_setting("filter", "text") == "text":
            return "skip", "Text filtered out"
        await _send_html_safe(client.send_message, dest_ent, text or "", reply_to=cfg.get("topic") or None)
        return "ok", None

    # 1) INSTANT path: bot-client file_id copy (unprotected content, zero download!)
    if used_bot and not cfg.get("rename"):
        try:
            CURRENT["phase"] = "✨ Instant Copy"
            await instant_copy(dest, msgs, text, cfg)
            return "ok", None
        except ChatForwardsRestrictedError:
            CURRENT["log"] = "Restricted mila — turbo engine on 🔥"
        except Exception as e:
            log.info("Instant copy fail (%s) — turbo path try karo", e)
        # Agar user session active hai to restricted fallback ke liye user_client se msgs fetch karo
        if fetcher is not client:
            try:
                u_chat = await safe_call(fetcher.get_entity, resolve_for(fetcher, chat_ref))
                u_msgs = await fetch_msg_group(fetcher, u_chat, msg_id)
                if u_msgs:
                    msgs = u_msgs
            except Exception:
                pass

    # 2) TURBO path: per-item download + re-upload + instant disk cleanup
    dl_client = fetcher if not used_bot or fetcher is not client else client
    item_dir = os.path.join(DL_DIR, f"it_{msg_id}_{uuid.uuid4().hex[:6]}")
    try:
        paths = await turbo_download(dl_client, msgs, item_dir, cfg=cfg)
        if not paths:
            if get_setting("filter", "text") == "text":
                return "skip", "Text filtered out"
            await _send_html_safe(client.send_message, dest_ent, text or "", reply_to=cfg.get("topic") or None)
            return "ok", None
        await turbo_upload(paths, text, dest, cfg)
        return "ok", None
    finally:
        shutil.rmtree(item_dir, ignore_errors=True)

# ------------------------------------------------------------------ #
#  JOB RUNNER (parallel workers)                                      #
# ------------------------------------------------------------------ #
async def run_job(event, links=None, label=None, resume=None, cfg=None):
    if CURRENT["active"]:
        await event.reply("⏳ Ek job already chal rahi hai! /status dekho.")
        return

    cfg = cfg or {}
    # Resume: pura state DB se wapas; warna fresh start
    if resume:
        full_links = [tuple(x) for x in resume.get("links", [])]
        label = resume.get("label") or label or "resumed task"
        start_idx = min(int(resume.get("idx", 0)), len(full_links))
        work = full_links[start_idx:]
        target = resume.get("target") or get_setting("target")
        mode = resume.get("mode") or get_setting("mode", "turbo")
        caption = resume.get("caption") or get_setting("caption", "")
        cfg = resume.get("cfg") or cfg
    else:
        full_links = list(links or [])
        start_idx = 0
        work = full_links
        target = cfg.get("target") or get_setting("target")
        mode = cfg.get("mode") or get_setting("mode", "turbo")
        caption = cfg.get("caption") or get_setting("caption", "")

    if not target:
        await event.reply(
            "🎯 Pehle target channel set karo!\n\n"
            "Ye bhejo: <code>/settarget @yourchannel</code>\n"
            "(bot ko us channel me <b>admin</b> banao with post rights)",
            parse_mode="html",
        )
        return
    if not work:
        clear_pending()
        await event.reply("🎉 Ye job already complete thi — kuch bacha nahi!")
        return

    try:
        delay = float(get_setting("delay", "0.5"))
    except ValueError:
        delay = 0.5
    try:
        workers = max(1, min(int(get_setting("workers", "2")), 5))
    except ValueError:
        workers = 2

    reset_job()
    job_cfg = dict(cfg)
    job_cfg.setdefault("caption", caption)
    CURRENT.update(
        active=True, total=len(full_links), started=time.time(),
        label=label, target=str(target),
        samples=deque(maxlen=12), pending_links=full_links,
        idx_offset=start_idx, resumed=bool(resume),
        job_mode=mode, job_caption=caption, cfg=job_cfg,
    )
    if resume:
        CURRENT["ok"] = resume.get("ok", 0)
        CURRENT["fail"] = resume.get("fail", 0)
        CURRENT["skip"] = resume.get("skip", 0)
        CURRENT["log"] = resume.get("log", "Resuming...")

    save_pending()
    CURRENT["progress_msg"] = await event.reply(
        render_card(), parse_mode="html", buttons=STOP_KB,
    )
    log.info(
        "Job: %d/%d posts -> %s (mode=%s workers=%d)",
        len(work), len(full_links), target, mode, workers,
    )

    updater = asyncio.create_task(card_updater())
    queue = asyncio.Queue(maxsize=workers * 2)
    MODE_FORWARD = (mode == "forward")

    async def dl_worker():
        idx = start_idx
        while idx < len(full_links):
            if CURRENT["cancel"]:
                break
            chat_ref, msg_id = full_links[idx]
            await queue.put((chat_ref, msg_id, idx))
            idx += 1
            await asyncio.sleep(delay if delay > 0 else 0.05)
        for _ in range(workers):
            await queue.put(None)

    async def up_worker(wid):
        while True:
            item = await queue.get()
            if item is None:
                return
            if CURRENT["cancel"]:
                queue.task_done()
                return
            chat_ref, msg_id, _idx = item
            item_cancelled = False
            try:
                if MODE_FORWARD:
                    fetcher = get_fetcher()
                    chat = await safe_call(fetcher.get_entity, resolve_for(fetcher, chat_ref))
                    msgs = await fetch_msg_group(fetcher, chat, msg_id)
                    if not msgs:
                        raise ValueError("not found")
                    dest_ent = resolve_for(client, CURRENT["target"])
                    if len(msgs) > 1:
                        await safe_call(client.forward_messages, dest_ent, msgs)
                    else:
                        await safe_call(client.forward_messages, dest_ent, msgs[0])
                    CURRENT["ok"] += 1
                    CURRENT["log"] = f"Forwarded #{msg_id}"
                else:
                    status, reason = await transfer_item(
                        CURRENT["target"], chat_ref, msg_id,
                        cfg=CURRENT["cfg"], index=_idx + 1,
                    )
                    if status == "ok":
                        CURRENT["ok"] += 1
                        CURRENT["log"] = f"#{msg_id} transferred ✅"
                    elif status == "skip":
                        CURRENT["skip"] += 1
                        CURRENT["log"] = f"Skipped {CURRENT['skip']}: {reason}"
                    else:
                        CURRENT["fail"] += 1
                        CURRENT["log"] = f"#{msg_id}: {reason}"
            except (ChannelPrivateError, ValueError) as e:
                CURRENT["fail"] += 1
                CURRENT["log"] = f"#{msg_id}: inaccessible ({str(e)[:60]})"
            except asyncio.CancelledError:
                item_cancelled = True
                CURRENT["log"] = f"#{msg_id}: stop — resume se dobara hoga"
                raise
            except Exception as e:
                CURRENT["fail"] += 1
                CURRENT["log"] = f"#{msg_id}: {str(e)[:80]}"
            finally:
                item_bytes = CURRENT["dl"][1] + CURRENT["ul"][1]
                CURRENT["dl"] = (0, 0)
                CURRENT["ul"] = (0, 0)
                if not item_cancelled:
                    CURRENT["moved_total"] += item_bytes
                    CURRENT["done"] += 1
                    save_pending()
                queue.task_done()

    try:
        prod = asyncio.create_task(dl_worker())
        cons = [asyncio.create_task(up_worker(i)) for i in range(workers)]
        CURRENT["tasks"] = [prod] + cons
        await asyncio.gather(prod, *cons)
    except asyncio.CancelledError:
        pass
    finally:
        updater.cancel()
        # temp files cleanup
        for f in os.listdir(DL_DIR):
            try:
                os.remove(os.path.join(DL_DIR, f))
            except Exception:
                pass

    final_text = render_card(final=True)
    try:
        await CURRENT["progress_msg"].edit(final_text, parse_mode="html", buttons=None)
    except Exception:
        await event.reply(final_text, parse_mode="html")

    if not CURRENT["cancel"]:
        clear_pending()
        mins = (time.time() - CURRENT["started"]) / 60
        log.info("Job done: ok=%d skip=%d fail=%d (%.1f min)", CURRENT["ok"], CURRENT["skip"], CURRENT["fail"], mins)
    else:
        save_pending()  # resume ke liye state preserved
    reset_job()

# ------------------------------------------------------------------ #
#  ⏹ STOP BUTTON                                                      #
# ------------------------------------------------------------------ #
@client.on(events.CallbackQuery(pattern=b"stopjob"))
async def stop_button(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    CURRENT["cancel"] = True
    for t in list(CURRENT["tasks"]):
        try:
            t.cancel()
        except Exception:
            pass
    await event.answer("🛑 Stopped & Cleared!")
    try:
        await event.edit("🛑 <b>STOPPED &amp; CLEARED</b>\n<i>Task cancel ho gaya. State saved — /resume se continue kar sakte ho.</i>", parse_mode="html")
    except Exception:
        pass

# ------------------------------------------------------------------ #
#  ✨ FORWARD SETTINGS PANEL                                          #
# ------------------------------------------------------------------ #
PANEL = {}   # uid -> panel state

def panel_kb(st):
    return [
        [Button.inline("🔗 Promo +", b"pn_promo"), Button.inline("📝 Caption +", b"pn_caption")],
        [Button.inline("✏️ Rename +", b"pn_rename"), Button.inline("🔗 Hyperlink +", b"pn_link")],
        [Button.inline("🖼 Thumbnail +", b"pn_thumb"), Button.inline("📌 Dest Topic", b"pn_topic")],
        [Button.inline(f"👤 Sender: {st.get('sender', 'Bot')}", b"pn_sender")],
        [Button.inline("✅ SAVE ALL & START FORWARDING", b"pn_save")],
        [Button.inline("⬅ Back to First Link", b"pn_back"), Button.inline("❌ Cancel", b"pn_cancel")],
    ]

def render_panel(st):
    n = st["last_id"] - st["first_id"] + 1
    lines = [
        "✨ <b>FORWARD SETTINGS PANEL</b> ✨",
        f"🔗 <b>Range:</b> {st['first_id']} → {st['last_id']} ({n} posts)",
        f"📌 <b>Destination Topic:</b> {st.get('topic') or 'General'}",
        f"🔗 <b>Promo:</b> {st.get('promo') or 'Not set'}",
        f"📝 <b>Caption:</b> {st.get('caption') or 'Not set'}",
        f"✏️ <b>Rename:</b> {st.get('rename') or 'Not set'}",
        f"🔗 <b>Hyperlink:</b> {st.get('hyperlink') or 'Not set'}",
        f"🖼 <b>Thumbnail:</b> {st.get('thumb') or 'Original'}",
        "",
        "👉 Kisi option ko add/edit karne ke liye uska button dabayein.",
        "Sab optional settings ready hone par <b>SAVE ALL & START FORWARDING</b> dabayein.",
        "✅ First aur End link save ho gaye.",
    ]
    return "\n".join(lines)

async def show_panel(event, uid):
    st = PANEL.get(uid)
    if not st:
        return
    try:
        await event.edit(render_panel(st), parse_mode="html", buttons=panel_kb(st))
    except Exception:
        await event.reply(render_panel(st), parse_mode="html", buttons=panel_kb(st))

async def start_forward_flow(event, mode):
    """mode: 'public' | 'private'. First link maango."""
    uid = event.sender_id
    if not get_setting("target"):
        await event.reply(
            "🎯 Pehle <code>/setchannel</code> se destination set karein.",
            parse_mode="html",
        )
        return
    if mode == "private" and not USER_ACTIVE:
        await event.reply(
            "⚠️ Pehle <code>/login</code> se Telegram userbot connect karein.\n\n"
            "Private/protected content ke liye user session chahiye 🔥",
            parse_mode="html",
        )
        return
    PANEL[uid] = {"step": "first", "mode": mode}
    if mode == "public":
        await event.reply(
            "🌐 <b>PUBLIC FORWARD</b> — public channel direct copy (no download, instant!)\n\n"
            "📩 <b>First Post Link</b> bhejo (jaise <code>t.me/channel/10</code>):",
            parse_mode="html",
        )
    else:
        await event.reply(
            "🔒 <b>PRIVATE FORWARD</b> — private/group/protected transfer (download flow)\n\n"
            "📩 <b>First Post Link</b> bhejo (jaise <code>t.me/c/1234567890/10</code>):",
            parse_mode="html",
        )

@client.on(events.CallbackQuery(pattern=b"pn_(.+)"))
async def panel_buttons(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    uid = event.sender_id
    st = PANEL.get(uid)
    action = event.pattern_match.group(1).decode()
    if not st:
        await event.answer("Panel expired — /forward se dobara shuru karo", alert=True)
        return
    if action == "cancel":
        PANEL.pop(uid, None)
        await event.edit("❌ Process cancel ho gaya.")
        return
    if action == "back":
        st["step"] = "first"
        await event.edit("⬅ <b>First Post Link</b> dobara bhejo:", parse_mode="html")
        return
    if action == "sender":
        st["sender"] = "Anonymous" if st.get("sender", "Bot") == "Bot" else "Bot"
        await event.answer(f"Sender: {st['sender']}")
        await show_panel(event, uid)
        return
    if action == "save":
        await event.answer("🚀 Turbo Transfer shuru!")
        chat_ref = st["chat_ref"]
        links = [(chat_ref, i) for i in range(st["first_id"], st["last_id"] + 1)]
        cfg = {
            "mode": "turbo" if st["mode"] == "private" else "copy",
            "promo": st.get("promo", ""),
            "caption": st.get("caption", ""),
            "rename": st.get("rename", ""),
            "hyperlink": st.get("hyperlink", ""),
            "thumb": st.get("thumb", ""),
            "topic": st.get("topic"),
            "sender": st.get("sender", "Bot"),
        }
        # Rename rules (panel) — temporary apply
        if st.get("rename"):
            parts = st["rename"].split("|", 1)
            if len(parts) == 2:
                reps = get_replacements()
                reps.append([parts[0].strip(), parts[1].strip()])
                set_setting("replacements", json.dumps(reps))
        PANEL.pop(uid, None)
        await event.edit(f"🚀 <b>Turbo Task Started!</b> {len(links)} posts — live card neeche 👇", parse_mode="html")
        await run_job(event, links, f"{st['mode']} {chat_ref} {st['first_id']}→{st['last_id']}", cfg=cfg)
        return
    # Input prompts
    prompts = {
        "promo": ("🔗 <b>Promo link</b> bhejo (valid http/https URL):\n\nHar post ke neeche ye link lagega. Remove karne ke liye <code>skip</code> likho.", "promo"),
        "caption": ("📝 <b>Caption</b> bhejo (max 800 chars):\n\n<code>{caption}</code> = original layout. Remove karne ke liye <code>skip</code>.", "caption"),
        "rename": ("✏️ <b>Rename rule</b> bhejo — format: <code>OLD|NEW</code>\n\nExample: <code>@oldchannel|@newchannel</code>", "rename"),
        "link": ("🔗 <b>Hyperlink</b> bhejo — format: <code>Label|https://link</code>\n\nYa <code>all|https://link</code> (saare links par).", "hyperlink"),
        "thumb": ("🖼 <b>Thumbnail</b> direct JPG/PNG URL bhejo:\n\nSirf videos par lagega. Fail ho to source thumbnail rahega. Remove ke liye <code>skip</code>.", "thumb"),
        "topic": ("📌 <b>Destination Topic ID</b> bhejo (forum group):\n\n<code>/id</code> se topic ID milta hai. General ke liye <code>0</code> ya <code>skip</code>.", "topic"),
    }
    if action in prompts:
        text, key = prompts[action]
        st["step"] = f"set_{key}"
        await event.edit(text, parse_mode="html")

async def handle_panel_input(event):
    """Panel flow ke input handle karo. Return True agar handle hua."""
    uid = event.sender_id
    st = PANEL.get(uid)
    if not st:
        return False
    raw = (event.raw_text or "").strip()
    step = st.get("step")
    if step == "first":
        parsed = parse_post_link(raw)
        if not parsed:
            await event.reply("❌ Valid post link nahi mila! <code>t.me/channel/10</code> format me bhejo ya /cancel.", parse_mode="html")
            return True
        st["chat_ref"], st["first_id"] = parsed
        st["step"] = "last"
        await event.reply(
            "📩 <b>Last Link (End ID)</b> bhejo — jaise <code>t.me/channel/50</code> ya sirf <code>50</code>:",
            parse_mode="html",
        )
        return True
    if step == "last":
        parsed = parse_post_link(raw)
        if parsed:
            end_id = parsed[1]
        elif raw.isdigit():
            end_id = int(raw)
        else:
            await event.reply("❌ End ID nahi mila! Link ya sirf number bhejo.", parse_mode="html")
            return True
        if end_id < st["first_id"]:
            st["first_id"], end_id = end_id, st["first_id"]
        st["last_id"] = end_id
        st["step"] = "panel"
        await event.reply(render_panel(st), parse_mode="html", buttons=panel_kb(st))
        return True
    if step and step.startswith("set_"):
        key = step[4:]
        val = "" if raw.lower() == "skip" else raw
        if key == "topic":
            st["topic"] = int(val) if val.isdigit() and int(val) > 0 else None
        else:
            st[key] = val
        st["step"] = "panel"
        await event.reply(render_panel(st), parse_mode="html", buttons=panel_kb(st))
        return True
    return False

# ------------------------------------------------------------------ #
#  COMMANDS                                                           #
# ------------------------------------------------------------------ #
def menu_kb():
    return [
        [Button.inline("🚀 Start Forwarding", b"menu_start")],
        [Button.inline("🔐 Login", b"menu_login"), Button.inline("🎯 Set Target", b"menu_target")],
        [Button.inline("⚙️ Settings", b"menu_settings"), Button.inline("📊 Status", b"menu_status")],
        [Button.inline("❓ Help", b"menu_help")],
    ]

def menu_text():
    sess = "✅ Connected" if USER_ACTIVE else "❌ Not Connected"
    tgt = get_setting("target", "")
    tgt_line = f"🎯 <b>Destination:</b> <code>{tgt}</code>" if tgt else "🎯 <b>Destination:</b> ❌ Not Set"
    return (
        "🔥 <b>ULTRA HIGH-SPEED RESTRICTED-CONTENT EXTRACTOR</b> 🔥\n"
        "<i>Bina kisi restriction ke… 100% Secure Sessions</i>\n\n"
        f"👤 <b>Session:</b> {sess}\n"
        f"{tgt_line}\n"
        f"⚡ <b>Engine:</b> TURBO | Workers: <b>{get_setting('workers', '2')}</b>\n"
        f"🔴 <b>Live Watch:</b> {len(get_watch_list())} routes\n\n"
        "🚀 <b>Start Forwarding</b> dabao aur shuru karo!"
    )

@client.on(events.NewMessage(pattern=r"^/start$|^/help$"))
@owner_filter
async def cmd_help(event):
    await event.reply(menu_text(), parse_mode="html", buttons=menu_kb())

@client.on(events.CallbackQuery(pattern=b"menu_(.+)"))
async def menu_buttons(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    action = event.pattern_match.group(1).decode()
    if action == "start":
        await event.edit(
            "🚀 <b>START FORWARDING NOW</b>\n\n"
            "Apne source ke hisaab se mode choose karein:\n\n"
            "🌐 <b>Public Forward</b> — public channel direct copy\n"
            "🔒 <b>Private Forward</b> — private/group/protected transfer\n\n"
            "<i>TIP: Public channel ke liye /publicforward, private link/group/protected download flow ke liye /forward use karein.</i>",
            parse_mode="html",
            buttons=[
                [Button.inline("🌐 Public · /publicforward", b"mode_public"), Button.inline("🔒 Private · /forward", b"mode_private")],
                [Button.inline("⬅ Back to Main Menu", b"menu_home")],
            ],
        )
    elif action == "home":
        await event.edit(menu_text(), parse_mode="html", buttons=menu_kb())
    elif action == "login":
        await event.edit("🔐 <code>/login</code> chalao — phone number se user session connect hoga.", parse_mode="html")
    elif action == "target":
        await event.edit(
            "🎯 <b>DESTINATION TARGET SET KARO</b>\n\n"
            "Channel/Group set karne ke liye bhejo:\n"
            "<code>/setchannel @yourchannel</code> ya <code>/setchannel -1001234567890</code>\n\n"
            "Ya neeche button dabakar seedha apne private chat me save karo:",
            parse_mode="html",
            buttons=[
                [Button.inline("📥 Save to My Private Chat", b"save_private")],
                [Button.inline("⬅ Back to Main Menu", b"menu_home")],
            ],
        )
    elif action == "settings":
        await event.answer()
        await cmd_settings(event)
    elif action == "status":
        await event.answer()
        await cmd_status(event)
    elif action == "help":
        await event.answer()
        await cmd_help(event)

@client.on(events.CallbackQuery(pattern=b"mode_(.+)"))
async def mode_buttons(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    await event.answer()
    mode = event.pattern_match.group(1).decode()
    await start_forward_flow(event, mode)

@client.on(events.NewMessage(pattern=r"^/forward$"))
@owner_filter
async def cmd_forward(event):
    await start_forward_flow(event, "private")

@client.on(events.NewMessage(pattern=r"^/publicforward$"))
@owner_filter
async def cmd_publicforward(event):
    await start_forward_flow(event, "public")

@client.on(events.NewMessage(pattern=r"^/ping$"))
@owner_filter
async def cmd_ping(event):
    up = time.time() - START_TIME
    await event.reply(
        f"🏓 Pong! Uptime: <b>{up/3600:.1f} hrs</b> | "
        f"Engine: <b>TURBO ⚡</b> | Session: <b>{'USER 🔥' if USER_ACTIVE else 'bot'}</b>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/(?:settarget|setchannel)(?:\s+(.+))?$"))
@owner_filter
async def cmd_settarget(event):
    raw_arg = (event.pattern_match.group(1) or "").strip()
    if not raw_arg:
        await event.reply(
            "🎯 <b>DESTINATION TARGET SET KARO</b>\n\n"
            "Format: <code>/setchannel @yourchannel</code> ya <code>/setchannel -1001234567890</code>\n"
            "<i>(Channel/group ID janne ke liye wahan <code>/id</code> bhejo)</i>\n\n"
            "Ya neeche button dabakar seedha apne private chat me save karo:",
            parse_mode="html",
            buttons=[[Button.inline("📥 Save to My Private Chat", b"save_private")]],
        )
        return
    val = parse_channel_ref(raw_arg) or raw_arg
    set_setting("target", val)
    await event.reply(
        f"✅ <b>Destination Set:</b> <code>{val}</code>\n"
        "⚠️ Bot ko is channel me <b>admin with post permission</b> banao!",
        parse_mode="html",
        buttons=[[Button.inline("📥 Save to My Private Chat", b"save_private")]],
    )

@client.on(events.CallbackQuery(pattern=b"save_private"))
async def save_private(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    set_setting("target", str(event.chat_id))
    await event.answer("✅ Destination Set: My Private Chat")
    await event.edit("✅ <b>Destination Set:</b> My Private Chat 📥", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/id$"))
@owner_filter
async def cmd_id(event):
    """Chat & Topic details. Reply to a message → us chat + user UID."""
    msg = await event.get_reply_message()
    lines = ["🆔 <b>CHAT &amp; TOPIC DETAILS</b>"]
    try:
        if msg:
            chat = await msg.get_chat()
            lines.append(f"💬 <b>Chat ID:</b> <code>{chat.id}</code>")
            lines.append(f"📛 <b>Name:</b> {getattr(chat, 'title', None) or getattr(chat, 'first_name', '?')}")
            lines.append(f"🏷 <b>Type:</b> {type(chat).__name__}")
            # Topic ID (forum)
            topic_id = getattr(msg, "reply_to_msg_id", None)
            if topic_id:
                lines.append(f"📌 <b>Topic ID:</b> <code>{topic_id}</code>")
            if msg.sender_id:
                lines.append(f"👤 <b>User UID:</b> <code>{msg.sender_id}</code>")
            lines.append(f"🔗 <b>Post Link:</b> <code>t.me/c/{str(chat.id)[4:] if str(chat.id).startswith('-100') else chat.id}/{msg.id}</code>")
        else:
            chat = await event.get_chat()
            lines.append(f"💬 <b>Chat ID:</b> <code>{chat.id}</code>")
            lines.append(f"📛 <b>Name:</b> {getattr(chat, 'title', None) or getattr(chat, 'first_name', '?')}")
            lines.append(f"🏷 <b>Type:</b> {type(chat).__name__}")
            lines.append("<i>Topic ID ke liye kisi topic ke message par reply karke /id bhejo.</i>")
    except Exception as e:
        lines.append(f"❌ Error: {e}")
    await event.reply("\n".join(lines), parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/setmode\s+(turbo|forward|download|copy)$"))
@owner_filter
async def cmd_setmode(event):
    val = event.pattern_match.group(1)
    if val in ("download", "copy"):
        val = "turbo"
    set_setting("mode", val)
    info = {
        "turbo": "👑 Recommended — instant copy jab possible, warna download+re-upload (restricted bhi chalega)",
        "forward": "Direct forward — fast lekin 'Forwarded from' header dikhega",
    }
    await event.reply(f"⚙️ Mode: <b>{val}</b>\n💡 {info[val]}", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/setworkers\s+([1-5])$"))
@owner_filter
async def cmd_setworkers(event):
    val = int(event.pattern_match.group(1))
    set_setting("workers", str(val))
    await event.reply(
        f"⚡ Parallel workers: <b>{val}</b>\n"
        "💡 Zyada workers = chhoti files me zyada speed. Bade files ke liye 2 best.",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/setfilter\s+(text|all)$"))
@owner_filter
async def cmd_setfilter(event):
    val = event.pattern_match.group(1)
    set_setting("filter", val)
    if val == "text":
        await event.reply("⏭ Filter: <b>text-only posts SKIP</b> honge (jaise reference bot)", parse_mode="html")
    else:
        await event.reply("📨 Filter: <b>SAB kuch transfer</b> hoga (text bhi)", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/settypes\s+(.+)$"))
@owner_filter
async def cmd_settypes(event):
    """Message type filters: /settypes video,photo — sirf ye types; /settypes all"""
    arg = event.pattern_match.group(1).strip().lower()
    all_types = ["text", "document", "video", "photo", "audio", "voice", "animation", "sticker", "poll"]
    if arg == "all":
        set_type_filters(set(all_types))
        await event.reply("✅ Saare message types ON.")
        return
    wanted = {t.strip() for t in arg.split(",") if t.strip() in all_types}
    if not wanted:
        await event.reply(f"Types: {', '.join(all_types)}\nExample: <code>/settypes video,photo,document</code>", parse_mode="html")
        return
    set_type_filters(wanted)
    await event.reply(f"✅ Sirf ye types transfer honge: <b>{', '.join(sorted(wanted))}</b>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/setdelay\s+([\d.]+)$"))
@owner_filter
async def cmd_setdelay(event):
    set_setting("delay", event.pattern_match.group(1))
    await event.reply(f"⏱ Delay: <b>{event.pattern_match.group(1)}s</b> per item", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/setpoll\s+([\d.]+)$"))
@owner_filter
async def cmd_setpoll(event):
    val = max(float(event.pattern_match.group(1)), 3)
    set_setting("poll", str(val))
    await event.reply(f"🔄 Live watch interval: <b>{val}s</b>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/setcaption\s+(.+)$"))
@owner_filter
async def cmd_setcaption(event):
    val = event.pattern_match.group(1).strip()
    set_setting("caption", val)
    await event.reply(f"✏️ Caption set!\n\n{val}")

@client.on(events.NewMessage(pattern=r"^/clearcaption$"))
@owner_filter
async def cmd_clearcaption(event):
    set_setting("caption", "")
    await event.reply("🧹 Caption clear!")

@client.on(events.NewMessage(pattern=r"^/replace\s+(.+)$"))
@owner_filter
async def cmd_replace(event):
    parts = event.pattern_match.group(1).split("|", 1)
    if len(parts) != 2:
        await event.reply("Format: <code>/replace old_text | new_text</code>", parse_mode="html")
        return
    reps = get_replacements()
    reps.append([parts[0].strip(), parts[1].strip()])
    set_setting("replacements", json.dumps(reps))
    await event.reply(f"🔁 Replace rule added ({len(reps)} total)")

@client.on(events.NewMessage(pattern=r"^/clearreplace$"))
@owner_filter
async def cmd_clearreplace(event):
    set_setting("replacements", "[]")
    await event.reply("🧹 Replace rules clear!")

@client.on(events.NewMessage(pattern=r"^/settings$"))
@owner_filter
async def cmd_settings(event):
    pend = load_pending()
    await event.reply(
        "⚙️ <b>TURBO ENGINE SETTINGS</b>\n\n"
        f"🎯 Target: <code>{get_setting('target', '— not set —')}</code>\n"
        f"⚙️ Mode: <b>{get_setting('mode', 'turbo')}</b>\n"
        f"⚡ Workers: <b>{get_setting('workers', '2')}</b>\n"
        f"⏭ Filter: <b>{get_setting('filter', 'text')}</b> (text = skip)\n"
        f"🎞 Types: <b>{', '.join(sorted(get_type_filters()))}</b>\n"
        f"⏱ Delay: <b>{get_setting('delay', '0.5')}s</b>\n"
        f"🔄 Poll: <b>{get_setting('poll', '8')}s</b>\n"
        f"✏️ Caption: <code>{get_setting('caption', '') or '—'}</code>\n"
        f"🔁 Replaces: <b>{len(get_replacements())}</b>\n"
        f"🔴 Watching: <b>{len(get_watch_list())}</b> routes\n"
        f"👤 Session: <b>{'USER 🔥' if USER_ACTIVE else 'bot'}</b>\n"
        f"🔄 Pending task: <b>{'HAAN — /resume se continue' if pend else 'nahi'}</b>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/status$"))
@owner_filter
async def cmd_status(event):
    if CURRENT["active"]:
        await event.reply(render_card(), parse_mode="html", buttons=STOP_KB)
    else:
        pend = load_pending()
        await event.reply(
            f"😴 Koi job active nahi.\n🔴 Live watch: <b>{len(get_watch_list())}</b> routes\n"
            + ("🔄 <b>Pending task saved hai — /resume se continue karo!</b>" if pend else ""),
            parse_mode="html",
        )

@client.on(events.NewMessage(pattern=r"^/cancel$|^/stop$"))
@owner_filter
async def cmd_cancel(event):
    if CURRENT["active"]:
        CURRENT["cancel"] = True
        for t in list(CURRENT["tasks"]):
            try:
                t.cancel()
            except Exception:
                pass
        await event.reply(
            "🛑 <b>Stopped!</b> State saved — <code>/resume</code> se continue kar sakte ho.",
            parse_mode="html",
        )
    else:
        await event.reply("Koi job active nahi 🙂")

@client.on(events.NewMessage(pattern=r"^/stats$"))
@owner_filter
async def cmd_stats(event):
    up = time.time() - START_TIME
    await event.reply(
        f"📊 <b>BOT STATS</b>\n"
        f"⏱ Uptime: <b>{up/3600:.1f} hrs</b>\n"
        f"👑 Engine: <b>TURBO</b> | Workers: <b>{get_setting('workers', '2')}</b>\n"
        f"🔴 Watch: <b>{len(get_watch_list())}</b> routes\n"
        f"👤 Session: <b>{'USER 🔥' if USER_ACTIVE else 'bot'}</b>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/myplan$"))
@owner_filter
async def cmd_myplan(event):
    await event.reply(
        "👑 <b>YOUR PLAN: TURBO UNLIMITED</b>\n\n"
        "✅ Unlimited transfers\n"
        "✅ Unlimited public + private forwarding\n"
        "✅ Full turbo speed\n"
        "✅ 24/7 live watch\n\n"
        "<i>Apna personal bot — koi limit nahi! 🔥</i>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/reset$"))
@owner_filter
async def cmd_reset(event):
    clear_pending()
    set_setting("caption", "")
    set_setting("replacements", "[]")
    await event.reply(
        "✅ <b>Account Data Refreshed Successfully!</b>\n\n"
        "Session/target/tasks clear ho gaye. <i>Premium safe.</i> 🔥",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/cleanup_ram$"))
@owner_filter
async def cmd_cleanup(event):
    for f in os.listdir(DL_DIR):
        try:
            os.remove(os.path.join(DL_DIR, f))
        except Exception:
            pass
    await event.reply("🧹 <b>Server memory flushed!</b> Temp files clear. ⚡", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/dyno_status$"))
@owner_filter
async def cmd_dyno(event):
    up = time.time() - START_TIME
    await event.reply(
        f"🖥 <b>SERVER STATUS</b>\n"
        f"⏱ Uptime: <b>{up/3600:.1f} hrs</b>\n"
        f"⚡ Engine: <b>TURBO</b> | Workers: <b>{get_setting('workers', '2')}</b>\n"
        f"👤 Session: <b>{'USER 🔥' if USER_ACTIVE else 'bot'}</b>\n"
        f"🔴 Watch routes: <b>{len(get_watch_list())}</b>\n"
        f"💾 Temp files: <b>{len(os.listdir(DL_DIR))}</b>",
        parse_mode="html",
    )

# --- 🔄 RESUME / DISCARD ---
@client.on(events.NewMessage(pattern=r"^/resume$"))
@owner_filter
async def cmd_resume(event):
    pend = load_pending()
    if not pend:
        await event.reply("Koi pending task nahi mila 🙂")
        return
    total = len(pend.get("links", []))
    idx = pend.get("idx", 0)
    left = max(total - idx, 0)
    if left == 0:
        clear_pending()
        await event.reply("🎉 Pending task already complete tha!")
        return
    await event.reply(
        f"🔄 <b>RESUMING!</b>\n"
        f"📌 {pend.get('label', 'task')} — <b>{left}</b> posts bache "
        f"({idx}/{total} already done) ⚡",
        parse_mode="html",
    )
    await run_job(event, resume=pend)

@client.on(events.NewMessage(pattern=r"^/discard$"))
@owner_filter
async def cmd_discard(event):
    if CURRENT["active"]:
        await event.reply("Job chal rahi hai — ⏹ button ya /cancel use karo!")
        return
    clear_pending()
    await event.reply("🧹 Pending task clear kar diya.")

# --- 🔴 WATCH (multi-route) ---
@client.on(events.NewMessage(pattern=r"^/watch\s+(.+)$"))
@owner_filter
async def cmd_watch(event):
    parts = event.pattern_match.group(1).strip().split()
    src = parse_channel_ref(parts[0])
    if not src:
        await event.reply(
            "Format: <code>/watch @channel</code> ya <code>/watch @channel @dest</code>\n"
            "(dusra target na do to global /setchannel target use hoga)",
            parse_mode="html",
        )
        return
    target = parse_channel_ref(parts[1]) if len(parts) > 1 else get_setting("target")
    if not target:
        await event.reply("🎯 Pehle /setchannel chalao ya target do!", parse_mode="html")
        return
    fetcher = get_fetcher()
    try:
        msgs = await safe_call(fetcher.get_messages, resolve_for(fetcher, src), limit=1)
        if not msgs:
            raise ValueError
        lastids = get_lastids()
        lastids[src] = msgs[0].id
        save_lastids(lastids)
    except Exception:
        await event.reply(
            f"❌ <code>{src}</code> readable nahi!\n\nFix: bot ko admin banao, YA "
            "<code>/login</code> se user session do 🔥",
            parse_mode="html",
        )
        return
    wl = get_watch_list()
    if not any(r["src"] == src for r in wl):
        wl.append({"src": src, "target": target})
        save_watch_list(wl)
    await event.reply(
        f"🔴 <b>LIVE WATCH ON!</b>\n📡 Source: <code>{src}</code>\n"
        f"🎯 Destination: <code>{target}</code>\n\n"
        "⚡ Naya post aate hi TURBO transfer ho jayega. 24/7. Automatic. 🔥",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/unwatch(?:\s+(.+))?$"))
@owner_filter
async def cmd_unwatch(event):
    arg = (event.pattern_match.group(1) or "").strip()
    wl = get_watch_list()
    if arg.lower() == "all":
        save_watch_list([])
        await event.reply("🧹 Saare watch routes hata diye.")
        return
    src = parse_channel_ref(arg) if arg else None
    new_wl = [r for r in wl if r["src"] != src]
    if src and len(new_wl) != len(wl):
        save_watch_list(new_wl)
        await event.reply(f"✅ <code>{src}</code> watch se hata diya.", parse_mode="html")
    else:
        await event.reply("/watchlist dekho, phir <code>/unwatch @channel</code> ya <code>/unwatch all</code>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/watchlist$"))
@owner_filter
async def cmd_watchlist(event):
    wl = get_watch_list()
    if not wl:
        await event.reply("📡 Koi source watch nahi ho raha.\n<code>/watch @channel</code> se shuru karo!", parse_mode="html")
        return
    lines = [f"🔴 <b>LIVE WATCH ROUTES ({len(wl)})</b>\n"]
    lines.extend(f"📡 <code>{r['src']}</code> → 🎯 <code>{r['target']}</code>" for r in wl)
    await event.reply("\n".join(lines), parse_mode="html")

# ------------------------------------------------------------------ #
#  🔥 USER SESSION LOGIN                                              #
# ------------------------------------------------------------------ #
LOGIN = {}

async def init_user_client(session_str):
    global user_client, USER_ACTIVE
    if user_client:
        try:
            await user_client.disconnect()
        except Exception:
            pass
    user_client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await user_client.connect()
    if await user_client.is_user_authorized():
        USER_ACTIVE = True
        me = await user_client.get_me()
        log.info("🔥 USER session active: %s", me.first_name)
        return True
    USER_ACTIVE = False
    log.warning("User session invalid/expired")
    return False

async def finish_login(event, tmp):
    s = tmp.session.save()
    try:
        await tmp.disconnect()
    except Exception:
        pass
    set_setting("user_session", s)
    ok = await init_user_client(s)
    LOGIN.clear()
    if ok:
        await event.reply(
            "✅ <b>Login Successful!</b>\n\n"
            "🔥 <b>USER SESSION ACTIVE!</b>\n\n"
            "✅ Public channels bina admin ke bhi read honge\n"
            "✅ Restricted content save (jo aapko dikhta hai)\n"
            "✅ Turbo engine ab full power pe hai ⚡\n\n"
            "🔴 Ab /forward, /watch lagao ya txt file bhejo!",
            parse_mode="html",
        )
    else:
        await event.reply("⚠️ Session save hua par authorize nahi. /login dobara.")

async def handle_login_step(event):
    raw = (event.raw_text or "").strip()
    step = LOGIN.get("step")
    tmp = LOGIN.get("client")
    if step == "phone":
        phone = raw.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            await event.reply("📞 <code>+91...</code> format me bhejo:", parse_mode="html")
            return
        try:
            await safe_call(tmp.send_code_request, phone)
            LOGIN.update(step="code", phone=phone)
            try:
                await event.message.delete()
            except Exception:
                pass
            await event.reply(
                "📨 Code official Telegram app me aaya (bot pe nahi)!\n"
                "Code bhejo (spaces me bhi chalega — jaise <code>1 2 3 4 5</code>):",
                parse_mode="html",
            )
        except Exception as e:
            LOGIN.clear()
            await event.reply(f"❌ Fail: <code>{e}</code>\n/login se dobara.", parse_mode="html")
    elif step == "code":
        code = raw.replace(" ", "")
        try:
            await tmp.sign_in(phone=LOGIN["phone"], code=code)
        except SessionPasswordNeededError:
            LOGIN["step"] = "password"
            try:
                await event.message.delete()
            except Exception:
                pass
            await event.reply(
                "🔐 <b>Enter 2FA Password!</b>\nExample: <code>mypassword123</code>\n\n"
                "<i>(Turant delete kar dunga)</i>",
                parse_mode="html",
            )
            return
        except (PhoneCodeInvalidError, PhoneCodeEmptyError):
            await event.reply("❌ Galat code! Dobara bhejo:")
            return
        except Exception as e:
            LOGIN.clear()
            await event.reply(f"❌ Login fail: <code>{e}</code>", parse_mode="html")
            return
        try:
            await event.message.delete()
        except Exception:
            pass
        await finish_login(event, tmp)
    elif step == "password":
        pwd = raw
        try:
            await tmp.sign_in(password=pwd)
        except Exception as e:
            await event.reply(f"❌ Galat: <code>{e}</code>\nDobara ya /cancellogin:")
            return
        try:
            await event.message.delete()
        except Exception:
            pass
        await finish_login(event, tmp)

@client.on(events.NewMessage(pattern=r"^/login$"))
@owner_filter
async def cmd_login(event):
    if USER_ACTIVE:
        await event.reply("✅ Already active! /logout pehle karo.")
        return
    if LOGIN:
        await event.reply("⏳ Login chal raha hai — phone/code/password bhejo ya /cancellogin.")
        return
    tmp = TelegramClient(StringSession(), API_ID, API_HASH)
    await tmp.connect()
    LOGIN["client"] = tmp
    LOGIN["step"] = "phone"
    await event.reply(
        "🔥 <b>USER SESSION LOGIN</b>\n\nPhone number bhejo (<code>+919876543210</code>):\n\n"
        "🔐 <i>Session sirf aapke bot me store hoga. Phone/code turant delete hoga.</i>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/logout$"))
@owner_filter
async def cmd_logout(event):
    global user_client, USER_ACTIVE
    if not USER_ACTIVE:
        await event.reply("Koi user session active nahi.")
        return
    try:
        await user_client.disconnect()
    except Exception:
        pass
    user_client = None
    USER_ACTIVE = False
    set_setting("user_session", "")
    await event.reply("✅ User session hata diya.")

@client.on(events.NewMessage(pattern=r"^/cancellogin$"))
@owner_filter
async def cmd_cancellogin(event):
    if LOGIN:
        try:
            await LOGIN.get("client").disconnect()
        except Exception:
            pass
        LOGIN.clear()
        await event.reply("🧹 Login cancel.")
    else:
        await event.reply("Koi login chal nahi raha.")

# ------------------------------------------------------------------ #
#  📁 GROUP TRANSFER (auto topics)                                    #
# ------------------------------------------------------------------ #
@client.on(events.NewMessage(pattern=r"^/grouptransfer\s+(.+)$"))
@owner_filter
async def cmd_grouptransfer(event):
    """Poora group + saare topics auto-transfer."""
    src = parse_channel_ref(event.pattern_match.group(1))
    target = get_setting("target")
    if not src:
        await event.reply("Format: <code>/grouptransfer @sourcegroup</code>", parse_mode="html")
        return
    if not target:
        await event.reply("🎯 Pehle /setchannel se destination set karein.", parse_mode="html")
        return
    fetcher = get_fetcher()
    try:
        src_chat = await safe_call(fetcher.get_entity, resolve_for(fetcher, src))
        dst_chat = await safe_call(client.get_entity, resolve_for(client, target))
    except Exception as e:
        await event.reply(f"❌ Chat access nahi: <code>{e}</code>\nBot ko admin banao ya /login karo.", parse_mode="html")
        return
    await event.reply(
        f"📁 <b>GROUP TRANSFER</b>\n📡 Source: <code>{src}</code>\n🎯 Destination: <code>{target}</code>\n\n"
        "🔍 Topics scan ho rahe hain...",
        parse_mode="html",
    )
    # Topics detect karo (forum topics)
    topics = {}  # topic_id -> {title, msgs}
    try:
        async for msg in fetcher.iter_messages(src_chat, limit=500):
            tid = getattr(msg, "reply_to_msg_id", None)
            if tid:
                topics.setdefault(tid, []).append(msg.id)
    except Exception as e:
        log.warning("grouptransfer scan: %s", e)
    if not topics:
        # Topics nahi mile — normal numbered transfer
        await event.reply("📁 Topics nahi mile — normal numbered transfer chal raha hai...", parse_mode="html")
        try:
            msgs = await safe_call(fetcher.get_messages, src_chat, limit=200)
            links = [(src, m.id) for m in msgs if m]
            links.sort(key=lambda x: x[1])
            await run_job(event, links, f"group {src} (numbered)")
        except Exception as e:
            await event.reply(f"❌ Fail: <code>{e}</code>", parse_mode="html")
        return
    # Har topic ke liye dest me topic banao/reuse + transfer
    await event.reply(f"📁 <b>{len(topics)} topics</b> mile! Auto-matching topics bana raha hoon...", parse_mode="html")
    for tid, mids in topics.items():
        if CURRENT["cancel"]:
            break
        # Source topic title
        try:
            tmsg = await safe_call(fetcher.get_messages, src_chat, ids=tid)
            title = (tmsg.message or f"Topic {tid}")[:60] if tmsg else f"Topic {tid}"
        except Exception:
            title = f"Topic {tid}"
        # Dest me same-name topic reuse ya create
        dst_topic = None
        try:
            async for tm in client.iter_messages(dst_chat, limit=100):
                if tm.id == tid and (tm.message or "") == title:
                    dst_topic = tm.id
                    break
        except Exception:
            pass
        if not dst_topic:
            try:
                created = await safe_call(client.send_message, dst_chat, title)
                dst_topic = created.id
            except Exception as e:
                log.warning("topic create fail: %s", e)
                dst_topic = None
        links = [(src, mid) for mid in sorted(mids)]
        cfg = {"topic": dst_topic, "caption": get_setting("caption", "")}
        await run_job(event, links, f"group topic: {title}", cfg=cfg)
    await event.reply("🎉 <b>Group Transfer Complete!</b> Saare topics transfer ho gaye. ✅", parse_mode="html")

# ------------------------------------------------------------------ #
#  🤖 BOT TRANSFER (kisi aur bot ka data)                             #
# ------------------------------------------------------------------ #
@client.on(events.NewMessage(pattern=r"^/bottransfer\s+(.+)$"))
@owner_filter
async def cmd_bottransfer(event):
    """Kisi aur BOT ke restricted chat-data extract karo."""
    bot_ref = event.pattern_match.group(1).strip().lstrip("@")
    if not USER_ACTIVE:
        await event.reply(
            "⚠️ Pehle <code>/login</code> se Telegram userbot connect karein.\n\n"
            "Bot ka data nikalne ke liye user session chahiye 🔥",
            parse_mode="html",
        )
        return
    if not get_setting("target"):
        await event.reply("🎯 Pehle /setchannel se destination set karein.", parse_mode="html")
        return
    try:
        bot_chat = await safe_call(user_client.get_entity, bot_ref)
    except Exception as e:
        await event.reply(f"❌ Bot <code>@{bot_ref}</code> nahi mila: <code>{e}</code>", parse_mode="html")
        return
    await event.reply(
        f"🤖 <b>BOT TRANSFER</b>\nBot: <code>@{bot_ref}</code> ✅\n\n"
        "📩 <b>First Message ID</b> bhejo (jaise <code>4</code>), ya <code>auto</code> likho (auto-select):",
        parse_mode="html",
    )
    uid = event.sender_id
    PANEL[uid] = {"step": "bot_first", "mode": "private", "chat_ref": bot_ref, "bot": bot_ref}

async def handle_bottransfer_input(event):
    uid = event.sender_id
    st = PANEL.get(uid)
    if not st or st.get("step") not in ("bot_first", "bot_last"):
        return False
    raw = (event.raw_text or "").strip()
    if st["step"] == "bot_first":
        if raw.lower() == "auto":
            st["first_id"] = 1
            st["step"] = "bot_last"
            await event.reply("📩 <b>Last Message ID</b> bhejo, ya <code>auto</code> (saara data):", parse_mode="html")
        elif raw.isdigit():
            st["first_id"] = int(raw)
            st["step"] = "bot_last"
            await event.reply("📩 <b>Last Message ID</b> bhejo:", parse_mode="html")
        else:
            await event.reply("❌ Message ID (number) ya <code>auto</code> bhejo.", parse_mode="html")
        return True
    if st["step"] == "bot_last":
        if raw.lower() == "auto":
            # Last ID = latest message
            try:
                msgs = await safe_call(user_client.get_messages, st["chat_ref"], limit=1)
                st["last_id"] = msgs[0].id if msgs else st["first_id"]
            except Exception:
                st["last_id"] = st["first_id"]
        elif raw.isdigit():
            st["last_id"] = int(raw)
        else:
            await event.reply("❌ Message ID (number) ya <code>auto</code> bhejo.", parse_mode="html")
            return True
        if st["last_id"] < st["first_id"]:
            st["first_id"], st["last_id"] = st["last_id"], st["first_id"]
        st["step"] = "panel"
        await event.reply(render_panel(st), parse_mode="html", buttons=panel_kb(st))
        return True
    return False

# ------------------------------------------------------------------ #
#  ⚡ CATCH-ALL: panel input + txt files + auto-detect links          #
# ------------------------------------------------------------------ #
@client.on(events.NewMessage(func=lambda e: e.is_private and is_owner(e.sender_id)))
async def catch_all(event):
    if LOGIN:
        await handle_login_step(event)
        return
    # Panel flow input?
    if event.sender_id in PANEL:
        if await handle_panel_input(event):
            return
        if await handle_bottransfer_input(event):
            return
    raw = event.raw_text or ""
    if raw.strip().startswith("/"):
        return

    # txt file
    doc = event.message.document
    if doc:
        name = ""
        for attr in doc.attributes:
            if getattr(attr, "file_name", None):
                name = attr.file_name
        mime = doc.mime_type or ""
        if name.endswith(".txt") or "text" in mime:
            data = await event.message.download_media(file=bytes)
            links = parse_links(data.decode("utf-8", errors="ignore"))
            if not links:
                await event.reply("🤔 Is txt me koi valid post link nahi mila!")
                return
            await event.reply(
                f"📄 <b>{name}</b> me <b>{len(links)}</b> links mile!\n"
                "👑 TURBO TRANSFER shuru... 🚀",
                parse_mode="html",
            )
            await run_job(event, links, name)
            return

    # auto-detect links
    links = parse_all_links(raw)
    if links:
        links = links[:100]
        if not get_setting("target"):
            await event.reply("🎯 Links mile! Pehle target set karo: <code>/settarget @channel</code>", parse_mode="html")
            return
        await event.reply(f"⚡ <b>{len(links)}</b> links pakde — TURBO transfer! 🚀", parse_mode="html")
        await run_job(event, links, f"auto-detect ({len(links)} links)")

# --- /save explicit ---
@client.on(events.NewMessage(pattern=r"^/save\s+(.+)$"))
@owner_filter
async def cmd_save(event):
    parsed = parse_post_link(event.pattern_match.group(1))
    if not parsed:
        await event.reply("Format: <code>/save t.me/channel/123</code>", parse_mode="html")
        return
    if not get_setting("target"):
        await event.reply("🎯 Pehle /settarget chalao!", parse_mode="html")
        return
    await event.reply("💾 Turbo save kar raha hoon...", parse_mode="html")
    await run_job(event, [parsed], f"save #{parsed[1]}")

# --- /batch range ---
@client.on(events.NewMessage(pattern=r"^/batch\s+(.+)$"))
@owner_filter
async def cmd_batch(event):
    arg = event.pattern_match.group(1)
    found = list(LINK_RE.finditer(arg))
    if len(found) < 2:
        await event.reply(
            "Format: <code>/batch t.me/ch/1 | t.me/ch/500</code>\n(start | end — same channel)",
            parse_mode="html",
        )
        return
    start_c, end_c = found[0], found[-1]
    if (start_c.group(1) or "", start_c.group(2).lower()) != (end_c.group(1) or "", end_c.group(2).lower()):
        await event.reply("❌ Start aur end channel alag hain! Same channel do.", parse_mode="html")
        return
    chat_ref = f"-100{start_c.group(2)}" if start_c.group(1) else start_c.group(2)
    a, b = int(start_c.group(3)), int(end_c.group(3))
    if a > b:
        a, b = b, a
    if b - a > 10000:
        await event.reply("😮 Max 10000 posts per batch!", parse_mode="html")
        return
    links = [(chat_ref, i) for i in range(a, b + 1)]
    await event.reply(f"📦 Batch ready: <b>{len(links)}</b> posts ({a}→{b}) 👑", parse_mode="html")
    await run_job(event, links, f"batch {chat_ref} {a}→{b}")

# ------------------------------------------------------------------ #
#  🔴 LIVE WATCH POLL LOOP                                            #
# ------------------------------------------------------------------ #
async def poll_source(src, target, delay):
    fetcher = get_fetcher()
    lastids = get_lastids()
    last = lastids.get(src, 0)
    try:
        msgs = await safe_call(fetcher.get_messages, resolve_for(fetcher, src), limit=10)
    except (ChannelPrivateError, ValueError):
        return
    except Exception as e:
        log.warning("poll %s: %s", src, e)
        return
    if not msgs:
        return
    if last == 0:
        lastids[src] = max(m.id for m in msgs)
        save_lastids(lastids)
        return
    new = sorted([m for m in msgs if m.id > last], key=lambda m: m.id)
    if not new:
        return
    sent = 0
    for m in new:
        if CURRENT["active"]:
            break  # job chal raha hai — baad me utha lenge (lastids safe)
        try:
            status, reason = await transfer_item(target, src, m.id)
            if status == "ok":
                sent += 1
        except Exception as e:
            log.warning("watch send %s/%s: %s", src, m.id, e)
        await asyncio.sleep(delay)
    lastids[src] = max(last, max(m.id for m in new))
    save_lastids(lastids)
    if sent:
        try:
            await client.send_message(
                OWNER_ID,
                f"🔴 <b>LIVE:</b> {sent} naye posts auto-forwarded from <code>{src}</code> ⚡",
                parse_mode="html",
            )
        except Exception:
            pass
        log.info("LIVE: %d posts from %s", sent, src)

async def poll_loop():
    log.info("🔴 LIVE watch loop started")
    while True:
        try:
            routes = get_watch_list()
            if routes and not CURRENT["active"]:
                try:
                    delay = max(float(get_setting("delay", "0.5")), 0.3)
                except ValueError:
                    delay = 0.5
                for r in routes:
                    if r.get("target"):
                        await poll_source(r["src"], r["target"], delay)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("poll_loop error")
        try:
            interval = max(float(get_setting("poll", "8")), 3)
        except ValueError:
            interval = 8
        await asyncio.sleep(interval)

async def keepalive_loop():
    """Render free instance ko sleep hone se bachane ke liye har 10 min health ping."""
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        return
    ping_url = f"{base_url}/health"
    log.info("⚡ Keep-alive active: %s", ping_url)
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(ping_url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    log.info("Keep-alive ping %s -> %s", ping_url, r.status)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.warning("Keep-alive warn: %s", e)

# ------------------------------------------------------------------ #
#  HTTP HEALTH SERVER                                                 #
# ------------------------------------------------------------------ #
async def health_handler(_request):
    return web.json_response({"status": "ok", "bot": "turbo-transfer", "v": "4.0"})

def run_http_server():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def start():
        app = web.Application()
        app.router.add_get("/", health_handler)
        app.router.add_get("/health", health_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        log.info("HTTP server on port %d ✅", PORT)

    loop.run_until_complete(start())
    loop.run_forever()

# ------------------------------------------------------------------ #
#  MAIN + AUTO-RESUME                                                 #
# ------------------------------------------------------------------ #
async def main():
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    log.info("✅ Bot logged in: @%s (id=%s)", me.username, me.id)

    # Register clickable slash-command menu in Telegram
    try:
        await client(SetBotCommandsRequest(
            scope=BotCommandScopeDefault(),
            lang_code="",
            commands=[
                BotCommand("start", "Main menu & dashboard"),
                BotCommand("publicforward", "Public channel direct copy"),
                BotCommand("forward", "Private/group/protected transfer"),
                BotCommand("setchannel", "Set destination channel/chat"),
                BotCommand("login", "Connect user session"),
                BotCommand("watch", "Live 24/7 auto-forward"),
                BotCommand("grouptransfer", "Full forum/group topic transfer"),
                BotCommand("bottransfer", "Extract messages from another bot"),
                BotCommand("status", "Live transfer status"),
                BotCommand("resume", "Resume pending task"),
                BotCommand("stop", "Instant stop & save state"),
                BotCommand("id", "Get Chat, Topic & User ID"),
                BotCommand("settings", "View engine settings"),
                BotCommand("myplan", "Check plan status"),
                BotCommand("cleanup_ram", "Clear temp cache"),
                BotCommand("dyno_status", "Server health & uptime"),
                BotCommand("reset", "Refresh settings"),
                BotCommand("logout", "Disconnect user session"),
            ],
        ))
        log.info("✅ Telegram command menu registered")
    except Exception as e:
        log.warning("Command menu setup skipped: %s", e)

    session_str = os.environ.get("USER_SESSION", "") or get_setting("user_session", "")
    if session_str:
        try:
            await init_user_client(session_str)
        except Exception as e:
            log.warning("User session restore fail: %s", e)

    client.loop.create_task(poll_loop())
    client.loop.create_task(keepalive_loop())

    # 🔄 AUTO-RESUME pending task (restart ke baad wahi se continue)
    pend = load_pending()
    if pend:
        total = len(pend.get("links", []))
        done = pend.get("idx", 0)
        left = max(total - done, 0)
        try:
            await client.send_message(
                OWNER_ID,
                "✅ <b>Bot restarted successfully!</b>\n"
                f"Your pending task can now continue — <b>{left}</b> posts bache "
                f"({done}/{total} done). Turbo transfer resume... ⚡",
                parse_mode="html",
            )
        except Exception:
            pass
        if left > 0:
            asyncio.create_task(run_job(
                type("E", (), {"reply": lambda self, *a, **k: client.send_message(OWNER_ID, *a, **k)})(),
                resume=pend,
            ))

    await client.run_until_disconnected()

if __name__ == "__main__":
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()
    client.loop.run_until_complete(main())
