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
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import (
    PeerChannel,
    MessageMediaWebPage,
    BotCommand,
    BotCommandScopeDefault,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
)
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    ChatForwardsRestrictedError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
    PasswordHashInvalidError,
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError,
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
USER_INFO = ""

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
RANGE_TOPIC_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/c/(\d+)/(\d+)/(\d+)\s*[-–—]\s*(\d+)",
    re.IGNORECASE,
)
RANGE_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/(c/)?([A-Za-z0-9_]{3,64})/(\d+)\s*[-–—]\s*(\d+)",
    re.IGNORECASE,
)
INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]{8,64})",
    re.IGNORECASE,
)
PLAIN_RE = re.compile(r"^@?([A-Za-z0-9_]{3,64})\s+(\d+)$", re.IGNORECASE)
PHONE_RE = re.compile(r"^\+\d{7,15}$")

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
    """Message me single links, topic links, aur range links (t.me/c/.../10-50) sab nikalo."""
    out = []
    # Range topic links first: t.me/c/123/23/10-50
    for m in RANGE_TOPIC_RE.finditer(text):
        chat_ref = f"-100{m.group(1)}"
        a, b = int(m.group(3)), int(m.group(4))
        if a > b:
            a, b = b, a
        for i in range(a, min(b, a + 2000) + 1):
            out.append((chat_ref, i))
    if out:
        return out
    # Range normal/private links: t.me/c/123/10-50 or t.me/chan/10-50
    for m in RANGE_LINK_RE.finditer(text):
        chat_ref = f"-100{m.group(2)}" if m.group(1) else m.group(2)
        a, b = int(m.group(3)), int(m.group(4))
        if a > b:
            a, b = b, a
        for i in range(a, min(b, a + 2000) + 1):
            out.append((chat_ref, i))
    if out:
        return out
    # Single links (line/token wise so topic links t.me/c/id/topic/msg don't misparse as topic_id)
    cleaned = TOPIC_LINK_RE.sub(
        lambda m: f"__TOPIC__{m.group(1)}__{m.group(3)}__", text
    )
    for m in re.finditer(r"__TOPIC__(\d+)__(\d+)__", cleaned):
        out.append((f"-100{m.group(1)}", int(m.group(2))))
    for m in LINK_RE.finditer(cleaned):
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

async def smart_get_entity(cl, ref):
    """Private channels (-100...) ka access_hash cache refresh ke saath entity lao."""
    target = resolve_for(cl, ref)
    try:
        return await safe_call(cl.get_entity, target)
    except ValueError:
        if cl is user_client and USER_ACTIVE:
            try:
                dialogs = await safe_call(cl.get_dialogs, limit=None)
                if isinstance(ref, str) and ref.strip().startswith("-100") and ref.strip()[4:].isdigit():
                    want_id = int(ref.strip()[4:])
                    for d in dialogs:
                        if getattr(d.entity, "id", None) == want_id:
                            return d.entity
                return await safe_call(cl.get_entity, target)
            except Exception:
                raise
        raise

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
    "seen_groups": set(),
}

def reset_job():
    CURRENT.update(
        active=False, cancel=False, done=0, total=0, ok=0, fail=0, skip=0,
        log="Starting Turbo Transfer...", label="", target="",
        phase="🔗 Connecting...", dl=(0, 0), ul=(0, 0), samples=None,
        progress_msg=None, started=0, tasks=[], resumed=False,
        moved_total=0, idx_offset=0, pending_links=[], cooldown_until=0,
        cfg={}, seen_groups=set(),
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
        start_w = max(1, msg_id - 9)
        window = list(range(start_w, msg_id + 10))
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

def _extract_media_attrs(m):
    """Restricted video/audio ke streaming, w, h, duration preserve karo."""
    attrs = []
    doc = getattr(m, "document", None)
    if not doc or not getattr(doc, "attributes", None):
        return attrs, False
    is_vid = False
    for a in doc.attributes:
        if isinstance(a, DocumentAttributeVideo):
            is_vid = True
            attrs.append(DocumentAttributeVideo(
                duration=getattr(a, "duration", 0) or 0,
                w=getattr(a, "w", 0) or 0,
                h=getattr(a, "h", 0) or 0,
                round_message=bool(getattr(a, "round_message", False)),
                supports_streaming=True,
            ))
        elif isinstance(a, DocumentAttributeAudio):
            attrs.append(a)
    return attrs, is_vid

async def turbo_download(fetcher, msgs, item_dir, cfg=None):
    """Album/singles + original thumbnail + streaming metadata download karo."""
    os.makedirs(item_dir, exist_ok=True)
    items = []
    want_orig_thumb = not (cfg or {}).get("thumb")
    for idx_m, m in enumerate(msgs):
        if not is_mediaable(m):
            continue
        fname_hint = ""
        if getattr(m, "file", None) and getattr(m.file, "name", None):
            fname_hint = f" ({m.file.name[:28]})"
        CURRENT["phase"] = f"📥 Downloading{fname_hint}"
        p = await safe_call(
            fetcher.download_media, m, file=item_dir,
            progress_callback=make_progress_cb("dl"),
        )
        if p:
            p = _rename_file_if_needed(p, cfg)
            orig_thumb = None
            if want_orig_thumb and idx_m == 0 and getattr(m, "document", None) and getattr(m.document, "thumbs", None):
                try:
                    orig_thumb = await safe_call(
                        fetcher.download_media, m, thumb=-1,
                        file=os.path.join(item_dir, f"orig_th_{m.id}.jpg"),
                    )
                except Exception:
                    orig_thumb = None
            attrs, is_vid = _extract_media_attrs(m)
            items.append({
                "path": p,
                "orig_thumb": orig_thumb,
                "attrs": attrs,
                "is_vid": is_vid,
            })
    return items

def _pick_uploader(cfg):
    if (cfg or {}).get("sender") in ("Userbot", "Anonymous") and USER_ACTIVE and user_client:
        return user_client
    return client

async def _upload_with_client(up_cl, items, text, dest, cfg):
    dest_ent = await smart_get_entity(up_cl, dest) if up_cl is user_client else resolve_for(up_cl, dest)
    reply_to = cfg.get("topic") or None
    custom_thumb = None
    if cfg.get("thumb"):
        custom_thumb = await get_thumb_path(cfg["thumb"])
    paths = [it["path"] for it in items]
    if len(paths) > 1:
        try:
            await safe_call(
                up_cl.send_file, dest_ent, paths,
                caption=text or "", parse_mode="html",
                supports_streaming=True,
                progress_callback=make_progress_cb("ul"),
                reply_to=reply_to,
            )
        except Exception:
            await safe_call(
                up_cl.send_file, dest_ent, paths,
                caption=text or "",
                supports_streaming=True,
                progress_callback=make_progress_cb("ul"),
                reply_to=reply_to,
            )
    elif items:
        it = items[0]
        thumb = custom_thumb or it.get("orig_thumb")
        kw = {
            "caption": text or "",
            "progress_callback": make_progress_cb("ul"),
            "reply_to": reply_to,
            "supports_streaming": True,
        }
        if thumb:
            kw["thumb"] = thumb
        if it.get("attrs"):
            kw["attributes"] = it["attrs"]
        try:
            await safe_call(up_cl.send_file, dest_ent, it["path"], parse_mode="html", **kw)
        except Exception:
            await safe_call(up_cl.send_file, dest_ent, it["path"], **kw)
    else:
        await _send_html_safe(up_cl.send_message, dest_ent, text or "", reply_to=reply_to)

async def turbo_upload(items, text, dest, cfg):
    CURRENT["phase"] = "📤 Uploading"
    up_cl = _pick_uploader(cfg)
    try:
        await _upload_with_client(up_cl, items, text, dest, cfg)
    except (ChatWriteForbiddenError, ChannelPrivateError, ValueError):
        if up_cl is client and USER_ACTIVE and user_client:
            log.info("Bot write fallback -> user_client for dest %s", dest)
            await _upload_with_client(user_client, items, text, dest, cfg)
        else:
            raise

async def transfer_item(dest, chat_ref, msg_id, cfg=None, index=None):
    """
    Ek post ko dest tak pahunchao (Public + Private + Restricted 'noforwards' sab!).
    Returns (status, reason): 'ok' | 'skip' | 'fail'
    """
    cfg = cfg or {}
    fetcher = get_fetcher()
    CURRENT["phase"] = "🔗 Connecting..."
    CURRENT["dl"] = (0, 0)
    CURRENT["ul"] = (0, 0)

    # Public channel ya /publicforward mode me pehle bot client se try karo (instant copy)
    bot_msgs = None
    b_chat = None
    is_public_ref = isinstance(chat_ref, str) and not chat_ref.startswith("-100") and not chat_ref.lstrip("-").isdigit()
    if fetcher is not client and (cfg.get("mode") == "copy" or is_public_ref):
        try:
            b_chat = await safe_call(client.get_entity, resolve_for(client, chat_ref))
            if not getattr(b_chat, "noforwards", False):
                bot_msgs = await fetch_msg_group(client, b_chat, msg_id)
        except Exception:
            bot_msgs = None

    chat_obj = None
    if bot_msgs and not getattr(bot_msgs[0], "noforwards", False):
        msgs = bot_msgs
        chat_obj = b_chat
        used_bot = True
    else:
        used_bot = (fetcher is client)
        try:
            chat_obj = await smart_get_entity(fetcher, chat_ref)
            msgs = await fetch_msg_group(fetcher, chat_obj, msg_id)
        except (ChannelPrivateError, ValueError):
            if not USER_ACTIVE:
                return "fail", "Private/Restricted chat — pehle /login karein"
            return "fail", f"Chat {chat_ref} me aapka account joined nahi hai (/join use karein)"
    if not msgs:
        return "fail", f"Message #{msg_id} empty/deleted"

    first = msgs[0]
    # Album deduplication in range/batch jobs
    gid = getattr(first, "grouped_id", None)
    if gid and CURRENT["active"]:
        seen = CURRENT.get("seen_groups")
        if isinstance(seen, set):
            if gid in seen:
                return "skip", "Album part already transferred"
            seen.add(gid)

    # Message-type filter
    enabled = get_type_filters()
    if msg_type(first) not in enabled:
        return "skip", f"Type filtered: {msg_type(first)}"

    # Caption source (album me kabhi caption 2nd item pe hota hai)
    cap_msg = first
    if not (first.message or "") and len(msgs) > 1:
        for m in msgs:
            if m.message:
                cap_msg = m
                break

    # Title (filename ya pehli line) — metadata caption ke liye
    title = ""
    if getattr(first, "file", None) and getattr(first.file, "name", None):
        title = apply_replacements(first.file.name)
    elif cap_msg.message:
        title = cap_msg.message.splitlines()[0][:80]

    # Original formatting (bold/italic/links) preserve in HTML
    raw_msg = cap_msg.message or ""
    if raw_msg and getattr(cap_msg, "entities", None):
        try:
            formatted_orig = tl_html.unparse(raw_msg, cap_msg.entities)
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
        await turbo_upload([], text, dest, cfg)
        return "ok", None

    is_restricted = bool(
        getattr(first, "noforwards", False)
        or getattr(chat_obj, "noforwards", False)
    )

    # 1) INSTANT path: bot-client file_id copy (jab restricted nahi hai aur rename nahi chahiye)
    if used_bot and not is_restricted and not cfg.get("rename") and cfg.get("sender", "Bot") == "Bot":
        try:
            CURRENT["phase"] = "✨ Instant Copy"
            await instant_copy(dest, msgs, text, cfg)
            return "ok", None
        except ChatForwardsRestrictedError:
            CURRENT["log"] = "Restricted content detected — Turbo Extractor ON 🔥"
        except Exception as e:
            log.info("Instant copy fail (%s) — turbo path try karo", e)
        if fetcher is not client:
            try:
                u_chat = await smart_get_entity(fetcher, chat_ref)
                u_msgs = await fetch_msg_group(fetcher, u_chat, msg_id)
                if u_msgs:
                    msgs = u_msgs
                    used_bot = False
            except Exception:
                pass

    # 2) TURBO RESTRICTED EXTRACTOR path: download via user_client + HD streaming re-upload + auto disk cleanup
    dl_client = fetcher if (not used_bot or fetcher is not client) else client
    if is_restricted:
        CURRENT["log"] = f"🔓 Extracting restricted #{msg_id}..."
    item_dir = os.path.join(DL_DIR, f"it_{msg_id}_{uuid.uuid4().hex[:6]}")
    try:
        items = await turbo_download(dl_client, msgs, item_dir, cfg=cfg)
        if not items:
            if get_setting("filter", "text") == "text":
                return "skip", "Text filtered out"
            await turbo_upload([], text, dest, cfg)
            return "ok", None
        await turbo_upload(items, text, dest, cfg)
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
            "🎯 Pehle destination set karein!\n"
            "<code>/setchannel @channel</code> bhejo ya neeche button dabao:",
            parse_mode="html",
            buttons=[[Button.inline("📥 Save to My Private Chat", b"save_private")]],
        )
        return
    if mode == "private" and not USER_ACTIVE:
        await event.reply(
            "🔒 <b>Private / Restricted Content Mode</b>\n\n"
            "Private channels ya <i>'Restrict saving content'</i> wale channels se extract karne ke liye pehle login karein 👇",
            parse_mode="html",
            buttons=[[Button.inline("🔐 Login Now (Fast OTP)", b"menu_login")]],
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
            "🔒 <b>PRIVATE &amp; RESTRICTED FORWARD</b> — private/group/copy-protected extractor 🔥\n\n"
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
        st["sender"] = "Userbot" if st.get("sender", "Bot") == "Bot" else "Bot"
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
    login_btn = Button.inline("👤 Session Active ✅", b"menu_login") if USER_ACTIVE else Button.inline("🔐 Login (Fast OTP)", b"menu_login")
    return [
        [Button.inline("🚀 Start Forwarding", b"menu_start")],
        [login_btn, Button.inline("🎯 Set Target", b"menu_target")],
        [Button.inline("📋 My Private Chats", b"menu_chats"), Button.inline("📊 Status", b"menu_status")],
        [Button.inline("⚙️ Settings", b"menu_settings"), Button.inline("❓ Help", b"menu_help")],
    ]

def menu_text():
    sess = f"✅ Connected ({html.escape(USER_INFO)})" if USER_ACTIVE else "❌ Not Connected (Tap 🔐 Login)"
    tgt = get_setting("target", "")
    tgt_line = f"🎯 <b>Destination:</b> <code>{tgt}</code>" if tgt else "🎯 <b>Destination:</b> ❌ Not Set"
    return (
        "🔥 <b>ULTRA HIGH-SPEED RESTRICTED-CONTENT EXTRACTOR v5.0</b> 🔥\n"
        "<i>Private Channels • Copy-Protected Content • 100% Secure Sessions</i>\n\n"
        f"👤 <b>Session:</b> {sess}\n"
        f"{tgt_line}\n"
        f"⚡ <b>Engine:</b> TURBO HD | Workers: <b>{get_setting('workers', '2')}</b>\n"
        f"🔴 <b>Live Watch:</b> {len(get_watch_list())} routes\n\n"
        "💡 <i>Seedha link/range (<code>t.me/c/.../10-50</code>) ya TXT file bhi bhej sakte ho!</i>"
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
        await event.answer()
        await event.edit(
            "🚀 <b>START FORWARDING NOW</b>\n\n"
            "Apne source ke hisaab se mode choose karein:\n\n"
            "🌐 <b>Public Forward</b> — public channel direct instant copy\n"
            "🔒 <b>Private / Restricted Forward</b> — private channel, group, ya copy-protected content HD extractor\n\n"
            "<i>TIP: Public channel ke liye /publicforward, aur private/restricted content ke liye /forward use karein.</i>",
            parse_mode="html",
            buttons=[
                [Button.inline("🌐 Public · /publicforward", b"mode_public"), Button.inline("🔒 Private · /forward", b"mode_private")],
                [Button.inline("⬅ Back to Main Menu", b"menu_home")],
            ],
        )
    elif action == "home":
        await event.answer()
        await event.edit(menu_text(), parse_mode="html", buttons=menu_kb())
    elif action == "login":
        await event.answer()
        await start_login_flow(event, edit=True)
    elif action == "chats":
        await event.answer("Loading chats...")
        await cmd_chats(event)
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
        src_ent = await smart_get_entity(fetcher, src)
        msgs = await safe_call(fetcher.get_messages, src_ent, limit=1)
        if not msgs:
            raise ValueError
        lastids = get_lastids()
        lastids[src] = msgs[0].id
        save_lastids(lastids)
    except Exception:
        await event.reply(
            f"❌ <code>{src}</code> readable nahi!\n\nFix: bot ko admin banao, YA "
            "neeche button se <b>Login</b> karke apna account jodo 🔥",
            parse_mode="html",
            buttons=[[Button.inline("🔐 Login Now", b"menu_login")]],
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
#  🔥 ULTRA-FAST USER SESSION LOGIN (Keypad + Resend + Session)       #
# ------------------------------------------------------------------ #
LOGIN = {}

def _new_login_client():
    return TelegramClient(
        StringSession(), API_ID, API_HASH,
        device_model="Turbo Forwarder Pro",
        system_version="Windows 11",
        app_version="5.0.0",
        lang_code="en",
        system_lang_code="en-US",
    )

async def _close_login_tmp():
    tmp = LOGIN.get("client")
    if tmp:
        try:
            await tmp.disconnect()
        except Exception:
            pass
    LOGIN.clear()

async def init_user_client(session_str):
    global user_client, USER_ACTIVE, USER_INFO
    if user_client:
        try:
            await user_client.disconnect()
        except Exception:
            pass
    user_client = TelegramClient(
        StringSession(session_str), API_ID, API_HASH,
        device_model="Turbo Forwarder Pro",
        system_version="Windows 11",
        app_version="5.0.0",
        lang_code="en",
        system_lang_code="en-US",
    )
    await user_client.connect()
    if await user_client.is_user_authorized():
        USER_ACTIVE = True
        me = await user_client.get_me()
        uname = f" @{me.username}" if getattr(me, "username", None) else ""
        USER_INFO = f"{me.first_name or 'User'}{uname}"
        log.info("🔥 USER session active: %s (id=%s)", USER_INFO, me.id)
        # Warm up private channels/groups entity cache in background so t.me/c/... works immediately!
        try:
            dialogs = await user_client.get_dialogs(limit=None)
            log.info("✅ Cached %d dialogs for private/restricted access", len(dialogs))
        except Exception as e:
            log.warning("Dialog cache warmup warn: %s", e)
        return True
    USER_ACTIVE = False
    USER_INFO = ""
    log.warning("User session invalid/expired")
    return False

def _otp_kb():
    return [
        [Button.inline("1️⃣", b"lg_d1"), Button.inline("2️⃣", b"lg_d2"), Button.inline("3️⃣", b"lg_d3")],
        [Button.inline("4️⃣", b"lg_d4"), Button.inline("5️⃣", b"lg_d5"), Button.inline("6️⃣", b"lg_d6")],
        [Button.inline("7️⃣", b"lg_d7"), Button.inline("8️⃣", b"lg_d8"), Button.inline("9️⃣", b"lg_d9")],
        [Button.inline("⌫ Back", b"lg_bk"), Button.inline("0️⃣", b"lg_d0"), Button.inline("✅ Verify", b"lg_ok")],
        [Button.inline("🔁 Resend OTP", b"lg_resend"), Button.inline("✏️ Change Phone", b"lg_chg")],
        [Button.inline("❌ Cancel Login", b"lg_cancel")],
    ]

def _otp_text(note=""):
    phone = LOGIN.get("phone", "")
    code_len = LOGIN.get("code_len", 5)
    buf = LOGIN.get("buf", "")
    via = LOGIN.get("via", "Official Telegram App Chat (777000)")
    slots = [buf[i] if i < len(buf) else "•" for i in range(max(code_len, len(buf)))]
    disp = " ".join(slots)
    extra = f"\n\n{note}" if note else ""
    return (
        "🔐 <b>FAST OTP VERIFICATION</b>\n"
        "<i>━━━━━━━━━━━━━━━━━━━━━━</i>\n"
        f"📱 <b>Phone:</b> <code>{html.escape(phone)}</code>\n"
        f"📨 <b>OTP Sent To:</b> {html.escape(via)}\n"
        f"🔢 <b>OTP Code:</b> <code>{disp}</code>\n\n"
        "👇 <b>Neeche Keypad buttons daba kar OTP enter karo!</b>\n"
        "<i>(Keypad se code kabhi expire nahi hota aur 5th digit dabate hi auto-login ho jayega ⚡)</i>\n\n"
        "💡 Type karna ho to space dekar bhejo: <code>1 2 3 4 5</code>\n"
        "⚠️ <i>Bina space ke plain OTP mat bhejna, warna Telegram security ke liye code expire kar deta hai!</i>"
        f"{extra}"
    )

def _describe_code_type(sent):
    tname = type(getattr(sent, "type", None)).__name__.lower()
    if "app" in tname:
        return "Telegram App (Official 'Telegram' 777000 chat check karo!)"
    if "sms" in tname:
        return "SMS Message"
    if "call" in tname:
        return "Phone Call"
    if "email" in tname:
        return "Email"
    return "Telegram App / SMS"

async def start_login_flow(event, edit=False):
    if USER_ACTIVE:
        txt = (
            "✅ <b>USER SESSION ALREADY ACTIVE!</b>\n\n"
            f"👤 <b>Account:</b> {html.escape(USER_INFO)}\n"
            "🔓 Private channels + Restricted (<i>no-forward</i>) content extraction READY hai! 🔥\n\n"
            "Neeche se apne private channels dekh sakte ho ya naya account jodne ke liye Logout karo:"
        )
        kb = [
            [Button.inline("📋 My Private Channels", b"menu_chats"), Button.inline("🔑 Backup Session", b"lg_export")],
            [Button.inline("🚪 Logout Session", b"lg_logout")],
            [Button.inline("⬅ Back to Menu", b"menu_home")],
        ]
        if edit:
            await event.edit(txt, parse_mode="html", buttons=kb)
        else:
            await event.reply(txt, parse_mode="html", buttons=kb)
        return

    await _close_login_tmp()
    LOGIN.update(step="phone", buf="")
    txt = (
        "🔐 <b>ULTRA-FAST TELEGRAM LOGIN</b>\n"
        "<i>━━━━━━━━━━━━━━━━━━━━━━</i>\n"
        "📞 Apna <b>Phone Number</b> country code ke saath abhi bhejo:\n\n"
        "• India: <code>+919876543210</code>\n"
        "• Bangladesh: <code>+8801343029369</code>\n"
        "• Any Country: <code>+1...</code>\n\n"
        "⚡ <i>Number bhejte hi turant OTP jayega aur Keypad khul jayega!</i>"
    )
    kb = [
        [Button.inline("🔑 Login with StringSession", b"lg_sess")],
        [Button.inline("❌ Cancel", b"lg_cancel")],
    ]
    if edit:
        await event.edit(txt, parse_mode="html", buttons=kb)
    else:
        await event.reply(txt, parse_mode="html", buttons=kb)

async def send_otp_to_phone(event, raw_phone, resend=False):
    phone = re.sub(r"[\s\-()]", "", raw_phone.strip())
    if not PHONE_RE.match(phone):
        await event.reply(
            "❌ Invalid format! Country code (<code>+</code>) ke saath number bhejo:\n"
            "Example: <code>+8801343029369</code> ya <code>+919876543210</code>",
            parse_mode="html",
            buttons=[[Button.inline("❌ Cancel", b"lg_cancel")]],
        )
        return
    status_msg = None
    if not resend:
        try:
            await event.message.delete()
        except Exception:
            pass
        status_msg = await client.send_message(
            event.chat_id,
            f"📡 <b>Sending OTP to</b> <code>{phone}</code> ...\n<i>Telegram server se connect ho raha hai ⚡</i>",
            parse_mode="html",
        )
    try:
        tmp = LOGIN.get("client")
        if not tmp or not resend:
            await _close_login_tmp()
            tmp = _new_login_client()
            await tmp.connect()
        elif not tmp.is_connected():
            await tmp.connect()

        sent = await safe_call(tmp.send_code_request, phone)
        code_len = getattr(getattr(sent, "type", None), "length", 5) or 5
        via = _describe_code_type(sent)
        LOGIN.update(
            client=tmp,
            step="code",
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            code_len=code_len,
            via=via,
            buf="",
        )
        note = "🔁 <b>Fresh OTP bhej diya gaya hai!</b>" if resend else ""
        if resend:
            await event.edit(_otp_text(note), parse_mode="html", buttons=_otp_kb())
        elif status_msg:
            LOGIN["msg_id"] = status_msg.id
            await status_msg.edit(_otp_text(note), parse_mode="html", buttons=_otp_kb())
        else:
            m = await event.reply(_otp_text(note), parse_mode="html", buttons=_otp_kb())
            LOGIN["msg_id"] = m.id
    except PhoneNumberInvalidError:
        await _close_login_tmp()
        err = "❌ <b>Phone number invalid hai!</b> Sahi Telegram registered number country code ke saath bhejo."
        if status_msg:
            await status_msg.edit(err, parse_mode="html", buttons=[[Button.inline("🔄 Retry Login", b"menu_login")]])
        else:
            await event.reply(err, parse_mode="html", buttons=[[Button.inline("🔄 Retry Login", b"menu_login")]])
    except Exception as e:
        await _close_login_tmp()
        err = f"❌ <b>OTP Error:</b> <code>{html.escape(str(e))}</code>"
        if status_msg:
            await status_msg.edit(err, parse_mode="html", buttons=[[Button.inline("🔄 Retry Login", b"menu_login")]])
        else:
            await event.reply(err, parse_mode="html", buttons=[[Button.inline("🔄 Retry Login", b"menu_login")]])

async def finish_login(chat_id, tmp, status_target=None):
    s = tmp.session.save()
    try:
        await tmp.disconnect()
    except Exception:
        pass
    LOGIN.clear()
    set_setting("user_session", s)
    ok = await init_user_client(s)
    if ok:
        priv_cnt = 0
        try:
            dialogs = await user_client.get_dialogs(limit=200)
            priv_cnt = sum(1 for d in dialogs if d.is_channel or d.is_group)
        except Exception:
            pass
        txt = (
            "🎉 <b>LOGIN SUCCESSFUL!</b>\n"
            "<i>━━━━━━━━━━━━━━━━━━━━━━</i>\n"
            f"👤 <b>Connected Account:</b> {html.escape(USER_INFO)}\n"
            f"📂 <b>Accessible Channels/Groups:</b> <b>{priv_cnt}</b> synced ✅\n\n"
            "🔥 <b>UNLOCKED SUPERPOWERS:</b>\n"
            "✅ Private Channels (<code>t.me/c/...</code>) full access\n"
            "✅ <i>Restrict Saving Content</i> (No-Forward) HD Video/PDF extraction\n"
            "✅ Original Thumbnail + Instant Video Streaming preserved\n"
            "✅ Private Invite Links (<code>/join</code>) &amp; Bot Data (<code>/bottransfer</code>)\n\n"
            "🚀 Ab seedha koi bhi private link bhejo ya neeche button dabao!"
        )
        kb = [
            [Button.inline("🔒 Start Private Forward", b"mode_private")],
            [Button.inline("📋 View My Private Channels", b"menu_chats")],
            [Button.inline("🏠 Main Menu", b"menu_home")],
        ]
        if status_target:
            try:
                await status_target.edit(txt, parse_mode="html", buttons=kb)
                return
            except Exception:
                pass
        await client.send_message(chat_id, txt, parse_mode="html", buttons=kb)
    else:
        await client.send_message(chat_id, "⚠️ Session authorize nahi ho paya. /login se dobara try karein.")

async def _verify_otp_code(chat_id, code, status_target=None):
    tmp = LOGIN.get("client")
    if not tmp:
        await client.send_message(chat_id, "⚠️ Login session expire ho gaya — /login se dobara shuru karo.")
        return
    if not tmp.is_connected():
        await tmp.connect()
    try:
        await tmp.sign_in(
            phone=LOGIN["phone"],
            code=code,
            phone_code_hash=LOGIN.get("phone_code_hash"),
        )
    except SessionPasswordNeededError:
        LOGIN["step"] = "password"
        LOGIN["buf"] = ""
        txt = (
            "🔐 <b>2-STEP VERIFICATION (2FA) PASSWORD</b>\n\n"
            "Aapke Telegram account par 2FA password laga hai.\n"
            "👇 Apna <b>Cloud Password</b> abhi type karke bhejo:\n\n"
            "<i>(Security ke liye aapka password message turant auto-delete ho jayega 🔒)</i>"
        )
        kb = [[Button.inline("❌ Cancel Login", b"lg_cancel")]]
        if status_target:
            try:
                await status_target.edit(txt, parse_mode="html", buttons=kb)
                return
            except Exception:
                pass
        await client.send_message(chat_id, txt, parse_mode="html", buttons=kb)
        return
    except (PhoneCodeInvalidError, PhoneCodeEmptyError):
        LOGIN["buf"] = ""
        txt = _otp_text("❌ <b>Galat OTP code!</b> Dobara sahi digits tap karo:")
        if status_target:
            try:
                await status_target.edit(txt, parse_mode="html", buttons=_otp_kb())
                return
            except Exception:
                pass
        await client.send_message(chat_id, txt, parse_mode="html", buttons=_otp_kb())
        return
    except PhoneCodeExpiredError:
        LOGIN["buf"] = ""
        txt = _otp_text(
            "⏰ <b>OTP Expire ho gaya!</b>\n"
            "<i>(Plain OTP chat me likhne se Telegram code expire kar deta hai — neeche <b>🔁 Resend OTP</b> dabao aur Keypad buttons use karo!)</i>"
        )
        if status_target:
            try:
                await status_target.edit(txt, parse_mode="html", buttons=_otp_kb())
                return
            except Exception:
                pass
        await client.send_message(chat_id, txt, parse_mode="html", buttons=_otp_kb())
        return
    except Exception as e:
        await _close_login_tmp()
        await client.send_message(
            chat_id,
            f"❌ <b>Login Failed:</b> <code>{html.escape(str(e))}</code>",
            parse_mode="html",
            buttons=[[Button.inline("🔄 Try Again", b"menu_login")]],
        )
        return

    await finish_login(chat_id, tmp, status_target=status_target)

@client.on(events.CallbackQuery(pattern=b"lg_(.+)"))
async def login_callbacks(event):
    if not is_owner(event.sender_id):
        await event.answer("⛔ Sirf owner!", alert=True)
        return
    act = event.pattern_match.group(1).decode()

    if act == "cancel":
        await _close_login_tmp()
        await event.answer("Login cancelled")
        await event.edit("❌ <b>Login cancel kar diya gaya.</b>", parse_mode="html", buttons=[[Button.inline("🏠 Main Menu", b"menu_home")]])
        return

    if act == "logout":
        global user_client, USER_ACTIVE, USER_INFO
        if user_client:
            try:
                await user_client.disconnect()
            except Exception:
                pass
        user_client = None
        USER_ACTIVE = False
        USER_INFO = ""
        set_setting("user_session", "")
        await event.answer("Logged out!")
        await event.edit("✅ <b>User session disconnect ho gaya.</b>", parse_mode="html", buttons=[[Button.inline("🔐 Login Again", b"menu_login"), Button.inline("🏠 Menu", b"menu_home")]])
        return

    if act == "export":
        s = get_setting("user_session", "") or os.environ.get("USER_SESSION", "")
        if not s:
            await event.answer("Session nahi mila!", alert=True)
            return
        await event.answer("Session sent below!")
        await client.send_message(
            event.chat_id,
            "🔑 <b>YOUR BACKUP STRING SESSION</b> (Kisi ke saath share mat karna!):\n\n"
            f"<code>{s}</code>\n\n"
            "<i>Kabhi bhi <code>/session &lt;string&gt;</code> se 1 sec me login kar sakte ho.</i>",
            parse_mode="html",
        )
        return

    if act == "sess":
        await _close_login_tmp()
        LOGIN.update(step="session_str")
        await event.answer()
        await event.edit(
            "🔑 <b>LOGIN VIA TELETHON STRING SESSION</b>\n\n"
            "Apna Telethon <b>StringSession</b> paste karke bhejo:\n"
            "<i>(Message turant delete ho jayega aur 1 second me login ho jayega ⚡)</i>",
            parse_mode="html",
            buttons=[[Button.inline("⬅ Back to Phone Login", b"menu_login"), Button.inline("❌ Cancel", b"lg_cancel")]],
        )
        return

    if act == "chg":
        await start_login_flow(event, edit=True)
        return

    if LOGIN.get("step") != "code":
        await event.answer("Session active nahi hai — Login dabayein", alert=True)
        return

    if act == "resend":
        await event.answer("Sending fresh OTP...")
        await send_otp_to_phone(event, LOGIN["phone"], resend=True)
        return

    buf = LOGIN.get("buf", "")
    code_len = LOGIN.get("code_len", 5)

    if act.startswith("d") and act[1:].isdigit():
        if len(buf) < 8:
            buf += act[1:]
            LOGIN["buf"] = buf
        if len(buf) >= code_len:
            await event.answer("⚡ Verifying OTP...")
            await event.edit(_otp_text("⏳ <b>Verifying OTP with Telegram...</b>"), parse_mode="html")
            await _verify_otp_code(event.chat_id, buf, status_target=event)
            return
        await event.answer(f"OTP: {buf}")
        await event.edit(_otp_text(), parse_mode="html", buttons=_otp_kb())
        return

    if act == "bk":
        LOGIN["buf"] = buf[:-1]
        await event.answer()
        await event.edit(_otp_text(), parse_mode="html", buttons=_otp_kb())
        return

    if act == "ok":
        if len(buf) < 4:
            await event.answer("Pehle pura OTP enter karo!", alert=True)
            return
        await event.answer("⚡ Verifying OTP...")
        await event.edit(_otp_text("⏳ <b>Verifying OTP with Telegram...</b>"), parse_mode="html")
        await _verify_otp_code(event.chat_id, buf, status_target=event)
        return

async def handle_login_step(event):
    raw = (event.raw_text or "").strip()
    step = LOGIN.get("step")
    if step == "session_str":
        try:
            await event.message.delete()
        except Exception:
            pass
        LOGIN.clear()
        ok = await init_user_client(raw)
        if ok:
            set_setting("user_session", raw)
            await event.reply(f"✅ <b>StringSession Connected!</b> ({html.escape(USER_INFO)}) 🔥", parse_mode="html", buttons=menu_kb())
        else:
            await event.reply("❌ Invalid/Expired StringSession! Phone OTP se login karein:", buttons=[[Button.inline("🔐 Phone Login", b"menu_login")]])
        return

    if step == "phone":
        await send_otp_to_phone(event, raw)
        return

    if step == "code":
        # Extract 4-8 digits even if spaced/hyphenated or inside forwarded Telegram text
        m = re.search(r"(?:code[:\s]+)?(\d(?:[\s\-]*\d){3,7})", raw, re.IGNORECASE)
        digits = re.sub(r"\D", "", m.group(1)) if m else re.sub(r"\D", "", raw)
        try:
            await event.message.delete()
        except Exception:
            pass
        if len(digits) < 4:
            await event.reply("🔢 OTP ke digits nahi mile — upar Keypad buttons tap karo ya <code>1 2 3 4 5</code> bhejo!", parse_mode="html")
            return
        await _verify_otp_code(event.chat_id, digits)
        return

    if step == "password":
        pwd = raw
        tmp = LOGIN.get("client")
        try:
            await event.message.delete()
        except Exception:
            pass
        if not tmp:
            await event.reply("⚠️ Login timeout — /login dobara karein.")
            return
        if not tmp.is_connected():
            await tmp.connect()
        try:
            await tmp.sign_in(password=pwd)
        except PasswordHashInvalidError:
            await event.reply(
                "❌ <b>Galat 2FA Password!</b> Dobara sahi Cloud Password bhejo:",
                parse_mode="html",
                buttons=[[Button.inline("❌ Cancel", b"lg_cancel")]],
            )
            return
        except Exception as e:
            await event.reply(
                f"❌ <b>2FA Error:</b> <code>{html.escape(str(e))}</code>\nDobara bhejo ya Cancel karo:",
                parse_mode="html",
                buttons=[[Button.inline("❌ Cancel", b"lg_cancel")]],
            )
            return
        await finish_login(event.chat_id, tmp)

@client.on(events.NewMessage(pattern=r"^/login(?:\s+(.+))?$"))
@owner_filter
async def cmd_login(event):
    arg = (event.pattern_match.group(1) or "").strip()
    if arg and not USER_ACTIVE:
        await send_otp_to_phone(event, arg)
        return
    await start_login_flow(event, edit=False)

@client.on(events.NewMessage(pattern=r"^/session\s+(.+)$"))
@owner_filter
async def cmd_session_import(event):
    s = event.pattern_match.group(1).strip()
    try:
        await event.message.delete()
    except Exception:
        pass
    ok = await init_user_client(s)
    if ok:
        set_setting("user_session", s)
        await client.send_message(event.chat_id, f"✅ <b>StringSession Active!</b> ({html.escape(USER_INFO)}) 🔥", parse_mode="html", buttons=menu_kb())
    else:
        await client.send_message(event.chat_id, "❌ Invalid StringSession! <code>/login</code> se OTP login karein.", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/exportsession$"))
@owner_filter
async def cmd_export_session(event):
    s = get_setting("user_session", "") or os.environ.get("USER_SESSION", "")
    if not s:
        await event.reply("❌ Koi active user session nahi hai. Pehle /login karein.")
        return
    await event.reply(
        "🔑 <b>YOUR STRING SESSION</b> (Private rakhein!):\n\n"
        f"<code>{s}</code>",
        parse_mode="html",
    )

@client.on(events.NewMessage(pattern=r"^/chats$|^/dialogs$"))
@owner_filter
async def cmd_chats(event):
    """Joined private/public channels & groups list with -100 IDs and Restricted tags."""
    if not USER_ACTIVE or not user_client:
        await event.reply(
            "🔒 Apne private channels dekhne ke liye pehle Login karein:",
            buttons=[[Button.inline("🔐 Login Now", b"menu_login")]],
        )
        return
    dialogs = await safe_call(user_client.get_dialogs, limit=60)
    rows = []
    for d in dialogs:
        if d.is_channel or d.is_group:
            ent = d.entity
            cid = f"-100{ent.id}" if getattr(ent, "broadcast", False) or getattr(ent, "megagroup", False) else str(-ent.id)
            lock = "🔒" if getattr(ent, "noforwards", False) else ("🔐" if not getattr(ent, "username", None) else "🌐")
            title = html.escape((d.name or "Untitled")[:34])
            rows.append(f"{lock} <b>{title}</b>\n   └ ID: <code>{cid}</code>")
            if len(rows) >= 25:
                break
    if not rows:
        await event.reply("Koi channel/group nahi mila.")
        return
    txt = (
        f"📋 <b>YOUR CHANNELS &amp; GROUPS ({len(rows)} shown)</b>\n"
        "<i>🔒 = Copy-Restricted | 🔐 = Private | 🌐 = Public</i>\n\n"
        + "\n".join(rows)
        + "\n\n💡 <i>Kisi bhi private channel ke post ka link copy karke seedha yahan bhej do — restricted content bhi forward ho jayega!</i>"
    )
    await event.reply(txt[:4000], parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/join\s+(.+)$"))
@owner_filter
async def cmd_join(event):
    """Join a private channel invite link via user_client."""
    if not USER_ACTIVE or not user_client:
        await event.reply(
            "🔒 Private invite link join karne ke liye pehle Login karein:",
            buttons=[[Button.inline("🔐 Login Now", b"menu_login")]],
        )
        return
    arg = event.pattern_match.group(1).strip()
    m = INVITE_RE.search(arg)
    if not m:
        await event.reply("Format: <code>/join https://t.me/+AbCdEfGh...</code>", parse_mode="html")
        return
    inv_hash = m.group(1)
    try:
        upd = await safe_call(user_client, ImportChatInviteRequest(inv_hash))
        chat = upd.chats[0] if getattr(upd, "chats", None) else None
        cid = f"-100{chat.id}" if chat else "joined"
        title = getattr(chat, "title", "Private Chat")
        await event.reply(
            f"✅ <b>Joined Successfully!</b>\n📛 <b>{html.escape(title)}</b>\n🆔 ID: <code>{cid}</code>\n\n"
            "Ab is channel ke post links seedha bhej kar extract kar sakte ho! 🔥",
            parse_mode="html",
        )
    except UserAlreadyParticipantError:
        await user_client.get_dialogs(limit=100)
        await event.reply("✅ Aapka account is private channel me pehle se joined hai! Seedha post link bhejo 🔥")
    except (InviteHashExpiredError, InviteHashInvalidError):
        await event.reply("❌ Invite link invalid ya expire ho chuka hai!")
    except Exception as e:
        await event.reply(f"❌ Join fail: <code>{html.escape(str(e))}</code>", parse_mode="html")

@client.on(events.NewMessage(pattern=r"^/logout$"))
@owner_filter
async def cmd_logout(event):
    global user_client, USER_ACTIVE, USER_INFO
    if not USER_ACTIVE:
        await event.reply("Koi user session active nahi.")
        return
    try:
        await user_client.disconnect()
    except Exception:
        pass
    user_client = None
    USER_ACTIVE = False
    USER_INFO = ""
    set_setting("user_session", "")
    await event.reply("✅ User session hata diya.")

@client.on(events.NewMessage(pattern=r"^/cancellogin$"))
@owner_filter
async def cmd_cancellogin(event):
    if LOGIN:
        await _close_login_tmp()
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
        src_chat = await smart_get_entity(fetcher, src)
        dst_chat = await smart_get_entity(user_client, target) if USER_ACTIVE and user_client and str(target).startswith("-100") else await safe_call(client.get_entity, resolve_for(client, target))
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
    raw = (event.raw_text or "").strip()
    if raw.startswith("/"):
        return

    if LOGIN:
        await handle_login_step(event)
        return

    # Auto-detect phone number (+880... / +91...) even if user didn't tap /login first!
    cleaned_phone = re.sub(r"[\s\-()]", "", raw)
    if PHONE_RE.match(cleaned_phone) and not USER_ACTIVE and event.sender_id not in PANEL:
        await send_otp_to_phone(event, cleaned_phone)
        return

    # Panel flow input?
    if event.sender_id in PANEL:
        if await handle_panel_input(event):
            return
        if await handle_bottransfer_input(event):
            return

    # Auto-detect private invite link (t.me/+... or t.me/joinchat/...)
    inv_m = INVITE_RE.search(raw)
    if inv_m and not parse_all_links(raw):
        if not USER_ACTIVE or not user_client:
            await event.reply(
                "🔒 Private invite link mila! Pehle Login karein taaki aapka account join/extract kar sake:",
                buttons=[[Button.inline("🔐 Login Now", b"menu_login")]],
            )
            return
        try:
            upd = await safe_call(user_client, ImportChatInviteRequest(inv_m.group(1)))
            chat = upd.chats[0] if getattr(upd, "chats", None) else None
            cid = f"-100{chat.id}" if chat else "joined"
            title = getattr(chat, "title", "Private Chat")
            await event.reply(
                f"✅ <b>Private Channel Joined!</b>\n📛 <b>{html.escape(title)}</b>\n🆔 ID: <code>{cid}</code>\n\n"
                "Ab post link ya range (<code>t.me/c/.../1-50</code>) bhej do! 🔥",
                parse_mode="html",
            )
        except UserAlreadyParticipantError:
            await user_client.get_dialogs(limit=100)
            await event.reply("✅ Pehle se joined hai! Seedha post link bhejo 🔥")
        except Exception as e:
            await event.reply(f"❌ Invite join error: <code>{html.escape(str(e))}</code>", parse_mode="html")
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
            if any(str(c).startswith("-100") for c, _ in links) and not USER_ACTIVE:
                await event.reply(
                    f"📄 <b>{html.escape(name)}</b> me private channel links hain!\n"
                    "Pehle neeche button se Login karein phir file dobara bhejein 👇",
                    parse_mode="html",
                    buttons=[[Button.inline("🔐 Login Now", b"menu_login")]],
                )
                return
            await event.reply(
                f"📄 <b>{html.escape(name)}</b> me <b>{len(links)}</b> links mile!\n"
                "👑 TURBO TRANSFER shuru... 🚀",
                parse_mode="html",
            )
            await run_job(event, links, name)
            return

    # auto-detect links (single, multiple, or range t.me/c/.../10-50)
    links = parse_all_links(raw)
    if links:
        links = links[:2000]
        if not get_setting("target"):
            await event.reply(
                "🎯 Links mile! Pehle target set karo: <code>/setchannel @channel</code> ya neeche button dabao:",
                parse_mode="html",
                buttons=[[Button.inline("📥 Save to My Private Chat", b"save_private")]],
            )
            return
        if any(str(c).startswith("-100") for c, _ in links) and not USER_ACTIVE:
            await event.reply(
                "🔒 <b>Private / Restricted Channel Link Detected!</b>\n\n"
                "Private channel se restricted videos/PDFs nikalne ke liye pehle 10 second me Login karein 👇",
                parse_mode="html",
                buttons=[[Button.inline("🔐 Login Now (Fast OTP)", b"menu_login")]],
            )
            return
        await event.reply(f"⚡ <b>{len(links)}</b> posts pakde — TURBO EXTRACTOR ON! 🚀", parse_mode="html")
        await run_job(event, links, f"auto ({len(links)} posts)")

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
        src_ent = await smart_get_entity(fetcher, src)
        msgs = await safe_call(fetcher.get_messages, src_ent, limit=10)
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
    return web.json_response({"status": "ok", "bot": "turbo-transfer", "v": "5.0"})

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
                BotCommand("login", "Fast OTP / Session login"),
                BotCommand("forward", "Private & restricted content extractor"),
                BotCommand("publicforward", "Public channel instant copy"),
                BotCommand("chats", "List joined private/restricted channels"),
                BotCommand("join", "Join private channel invite link"),
                BotCommand("setchannel", "Set destination channel/chat"),
                BotCommand("watch", "Live 24/7 auto-forward"),
                BotCommand("grouptransfer", "Full forum/group topic transfer"),
                BotCommand("bottransfer", "Extract messages from another bot"),
                BotCommand("status", "Live transfer status"),
                BotCommand("resume", "Resume pending task"),
                BotCommand("stop", "Instant stop & save state"),
                BotCommand("id", "Get Chat, Topic & User ID"),
                BotCommand("settings", "View engine settings"),
                BotCommand("exportsession", "Backup your StringSession"),
                BotCommand("reset", "Refresh settings"),
                BotCommand("logout", "Disconnect user session"),
            ],
        ))
        log.info("✅ Telegram command menu registered")
    except Exception as e:
        log.warning("Command menu setup skipped: %s", e)

    if not get_setting("target") and OWNER_ID:
        set_setting("target", str(OWNER_ID))

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
    else:
        try:
            await client.send_message(
                OWNER_ID,
                "⚡ <b>TURBO EXTRACTOR v5.0 LIVE &amp; UPGRADED!</b>\n\n"
                "✅ <b>Fast Keypad OTP Login</b> Ready\n"
                "✅ <b>Private + Restricted Content HD Extractor</b> Ready\n"
                f"🎯 <b>Default Target:</b> <code>{get_setting('target')}</code> (My Private Chat)\n\n"
                "👇 Neeche <b>🔐 Login (Fast OTP)</b> dabao, ya seedha apna phone number (<code>+880...</code> / <code>+91...</code>) bhej do!",
                parse_mode="html",
                buttons=menu_kb(),
            )
        except Exception as e:
            log.info("Startup owner notify skipped: %s", e)

    await client.run_until_disconnected()

if __name__ == "__main__":
    t = threading.Thread(target=run_http_server, daemon=True)
    t.start()
    client.loop.run_until_complete(main())
