# 👑 PRO TURBO TRANSFER PIPELINE v4.0 — ULTRA POWER

Reference videos (YT @srcfire_47 — 10 videos + 6 shorts, 257 frames deep-review) se banaya gaya **turbo transfer engine** — usse zyada powerful! 🔥

## ✨ Features

### 👑 PRO TURBO TRANSFER PIPELINE card (videos jaisa)
```
👑 PRO TURBO TRANSFER PIPELINE 👑
━━━━━━━━━━━━━━━━━━━━
📌 Source: <source>
🎯 Destination: <dest>
📊 Progress: 45/233
📥 Downloading: 82.7% (535.00 MB/647.83 MB)
⚡ Speed: 11.42 MB/s
🕐 Telegram cooldown: 12s • Auto-resume enabled
✅ Transferred: 40 | ⏭ Skipped: 5
⏱ Time Remaining: ~ 1h 33m 39s
📋 Status Log: Starting Turbo Transfer...
[⏹ Instant Stop & Clear]
```

### ⚡ Turbo Engine (3-speed strategy)
1. **✨ Instant Copy** — unprotected content **file_id se server-side copy** (koi download/upload nahi — INSTANT!)
2. **📥📤 Turbo Download+Upload** — restricted/protected content download karke re-upload (user session power se)
3. **⚡ Parallel Workers** — 1-5 files ek saath process (`/setworkers`)

### ✨ FORWARD SETTINGS PANEL (videos wala signature feature!)
`/forward` ya `/publicforward` → First Link → Last Link → **settings panel**:
```
✨ FORWARD SETTINGS PANEL ✨
🔗 Range: 10 → 50 (41 posts)
📌 Destination Topic: General
🔗 Promo: Not set
📝 Caption: Not set
✏️ Rename: Not set
🔗 Hyperlink: Not set
🖼 Thumbnail: Original
[🔗 Promo +] [📝 Caption +] [✏️ Rename +] [🔗 Hyperlink +]
[🖼 Thumbnail +] [📌 Dest Topic] [👤 Sender: Bot]
[✅ SAVE ALL & START FORWARDING]
```
- **Promo** — har post ke neeche link
- **Caption** — `{caption}`/`{original}`/`{index}`/`{title}` placeholders (max 800 chars)
- **Rename** — `OLD|NEW` (filename + text replace)
- **Hyperlink** — `Label|URL` ya `all|URL`
- **Thumbnail** — direct JPG/PNG URL (sirf videos)
- **Destination Topic** — forum group topic ID
- **Sender** — Bot / Anonymous toggle

### 🌐 Public vs 🔒 Private split
- **`/publicforward`** — public channel **direct copy** (no download, instant, no login needed)
- **`/forward`** — private/group/protected **download flow** (user session chahiye)
- Mode chooser: `/start` → 🚀 Start Forwarding

### 🔄 RESUME SYSTEM (videos wala — "Bot restarted successfully!")
- Har item ke baad state DB me save
- Bot restart/crash → **khud message aayega**: "Your pending task can now continue" → **auto-resume** ⚡
- Manual: `/resume` | hatao: `/discard`

### 📄 Forwarding ke saare raaste
- **TXT file** bhejo (har line ek post link) — bina command ke bhi chalega
- **Auto-detect**: koi bhi t.me link paste karo (100 tak ek saath)
- **Batch range**: `/batch t.me/ch/1 | t.me/ch/10000` (10k tak!)
- **🔴 LIVE 24/7 watch**: `/watch @source [@dest]` — multi-route, naya post aate hi auto-transfer
- **📁 Group transfer**: `/grouptransfer @group` — saare topics auto-match
- **🤖 Bot transfer**: `/bottransfer @somebot` — kisi aur bot ka restricted data

### 🔥 User Session (restricted content saver)
`/login` → apna Telegram account jodo → public channels bina admin ke read + restricted save. Phone/code/password messages **turant delete** 🔐

### ⚙️ Smart filters & controls
- ⏭ **Text filter**: text-only posts auto-skip (`Skipped: Text filtered out` — videos jaisa) — `/setfilter all` se off
- 🎞 **Type filters**: `/settypes video,photo,document` — sirf ye types
- ✏️ Caption + replace rules
- ⏱ Delay + poll interval control
- FloodWait auto-retry + **cooldown display** (ban-safe)
- ⏹ **Instant Stop & Clear** button + `/stop` (state saved, resume possible!)

### 🛠 Utility commands
| Command | Kaam |
|---|---|
| `/id` | Chat ID + Name + Type + **Topic ID** (reply → user UID) |
| `/setchannel` | Destination set (+ "Save to My Private Chat" button) |
| `/myplan` | Plan status (Unlimited) |
| `/reset` | Account data refresh (premium safe) |
| `/cleanup_ram` | Server memory flush |
| `/dyno_status` | Server health/speed |
| `/settings` | Saari settings ek jagah |
| `/status` `/stats` `/ping` | Live status |

## 🔑 Env Vars

| Key | Value | Kahan se |
|---|---|---|
| `API_ID` | number | [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | 32-char hex | same |
| `BOT_TOKEN` | `123:AAH...` | @BotFather |
| `OWNER_ID` | numeric TG id | @userinfobot |
| `USER_SESSION` | *(optional)* | `/login` se bhi ho jata hai |
| `SUDO_USERS` | *(optional)* | extra admins |
| `PORT` | 8080 | Render khud set karta hai |

## 🚀 Render Deploy (FREE)

1. Code GitHub pe push karo (PAT se)
2. [render.com](https://render.com) → New Web Service → repo select
3. Env vars add karo (upar wale) + `PORT=10000`
4. Start command: `python bot.py`
5. BotFather se `/setprivacy` → Disable (group me kaam ke liye)

## 📋 Full Command List

```
/start            — main menu (status card + buttons)
/forward          — private/protected transfer (settings panel)
/publicforward    — public direct copy (settings panel)
/setchannel       — destination set
/id               — chat/topic details
/login /logout    — user session
/watch /unwatch   — live 24/7 auto-forward (multi-route)
/grouptransfer    — poora group + topics
/bottransfer      — kisi bot ka data
/batch            — range transfer
/save             — single post save
/setmode          — turbo|forward
/setworkers       — parallel files (1-5)
/setfilter        — text|all
/settypes         — message type filter
/setcaption       — caption template
/replace          — OLD|NEW rules
/setdelay /setpoll
/resume /discard  — pending task
/stop /cancel     — instant stop
/settings /status /stats /ping
/myplan /reset /cleanup_ram /dyno_status
```

## ⚠️ Responsible Use
Sirf apna ya allowed content transfer karo. Doosron ke paid/restricted content ko bina permission ke copy karna copyright violation ho sakta hai. Ye bot personal/educational use ke liye hai.
