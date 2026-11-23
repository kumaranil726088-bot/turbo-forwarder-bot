# Turbo Forwarder V6.0 — Multi-User Edition

Telegram bot: **@TurboForwarderPro_Bot**. Open access is the default: `OWNER_ID` controls administration, not ordinary forwarding.

## Architecture

- `bot.py`: command/callback router, private-chat security gates, isolated logins/jobs/panels, ordered prefetch pipeline, event-driven live routes and catch-up, real forum creation, HTTP health service.
- `storage.py`: parameterized SQLite tables `users`, `user_settings(uid,key)`, `global_settings`; legacy V5 settings migrate to the owner once. Telegram sessions are Fernet-encrypted using `SESSION_KEY`. Plaintext legacy rows are securely removed after migration.
- `transfer.py`: public/private/topic/range link parsing, album helpers, bounded parallel MTProto download/upload using 512-KiB chunks, separate MTProto senders, cross-DC authorization export/import, normal Telethon fallback.
- `test_v6.py`: offline unit and mocked-integration regression suite. Run `python -m unittest test_v6 -v`.

Each user has a separate Telegram account session, destination, settings, active job, resume checkpoint, temporary directory and watch routes. A user cannot export another user's session or use shared bot admin privileges to gain access to unrelated destination channels. Imported/logged-in accounts must match the requesting Telegram user ID. Owner administration requires exactly `OWNER_ID`; forwarding is not owner-gated.

### Honest performance/permission limits

No unlimited-speed, sub-second, exact-once, or guaranteed restricted-content claim is made. Telegram flood waits, permissions, account limits, disk, CPU, bandwidth and hosting sleep still apply. The downloader can only download media Telegram permits that account to retrieve; membership is required for private sources. A protected-media re-upload may be rejected by Telegram. Use only content you are authorized to copy.

The native-forward/cached-media fast path is used only within the same Telegram session. Cross-account file references are **not** treated as reusable bot file IDs. User-source → bot-sender transfers download and upload when necessary. `cryptg` accelerates supported encryption operations; it does not remove API limits.

- Default two preparation workers/user, four transport connections/file, two global transfer slots, 0.1s minimum deliberate delay, FloodWait backoff.
- Single-file and album sends preserve available media attributes, captions/entities (unchanged captions), filenames, original video/audio attributes and source thumbnails. Custom thumbnail photos are resized to JPEG locally.
- Custom caption transformations produce plain text, or explicit hyperlinks with `Label|https://…`. Unmodified captions preserve original Telegram entities.
- Polls require native forward mode, User sender, no custom caption/replacement settings and no destination topic. Cloning quiz answers/hidden poll results is not supported.
- `/grouptransfer` copies up to the selected newest 10,000 messages, oldest first. For forum→forum it enumerates, matches and creates **real forum topics**. Non-forum destinations receive numbered captions. It does not claim unlimited full-history transfer.
- Stop cancels only that user's job. `/resume` uses the last confirmed checkpoint. Sending and checkpointing are not an atomic Telegram/database transaction: a crash after send but before checkpoint can produce a duplicate on recovery.
- Event-driven watchers debounce for 0.7 seconds for albums and send in order. Polling drains messages above the stored cursor, including restart backlog when storage survives. A failing message does not advance the cursor; `/watchlist` shows the error. Pause retains the cursor, so resume catches up. Re-add a route to intentionally start at the newest message.

## Setup

Requires Python 3.11+ (production uses the Render-configured Python).

```bash
pip install -r requirements.txt
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Put generated key in SESSION_KEY; fill API_ID/API_HASH/BOT_TOKEN/OWNER_ID.
python bot.py
```

Do not change `SESSION_KEY` after saving logins. The operator can access decrypted sessions from the running server; encryption at rest is not end-to-end protection against the operator. `/login` explains this to users. OTP input uses an inline keypad; phone/password input deletion is best-effort. Login temporary clients expire after ten minutes.

### IMPORTANT: persistence on Render

**Render free web-service filesystems are ephemeral.** Without a persistent disk/database, redeploy/restart can erase SQLite settings, sessions, routes and checkpoints. Encryption alone does not provide persistence. The app warns users and `/health` reports `persistence: ephemeral` by default.

For durable recovery, configure a Render persistent disk (requires a suitable paid service plan) and point `DB_PATH` and `DOWNLOAD_DIR` at its mount, e.g. `/var/data/botdata.db` and `/var/data/downloads`; set `PERSISTENT_STORAGE=1` only after doing this. The application does not purchase or upgrade hosting automatically. Existing databases migrate only if available at `DB_PATH`; it cannot recover a database already lost from an earlier ephemeral instance.

Scaling: one instance per database/session registry. Do not horizontally scale multiple instances over one SQLite file or run the same saved user session concurrently on different hosts. Increase resources and `MAX_SESSIONS` deliberately. Open access means all users may register; it does not imply infinite simultaneous logins (default 20).

## Quick start

In the bot's private chat:

1. `/start`
2. `/login`, send phone, enter OTP with keypad (and 2FA password if needed).
3. `/setchannel @yourchannel` or `/setchannel -1001234567890:42`. The logged-in user must have access/write rights (admin for broadcast channels). Add the bot as a posting admin if using Bot sender.
4. `/forward` → first link → end link/ID → optional settings → **Save & start**.
5. Automatic route: `/watch https://t.me/c/1234567890/55 @yourchannel:42`.
6. `/watchlist` to pause/resume/delete and inspect errors.

Default destination is the user's own private chat. User sender with this destination saves to Saved Messages instead. Settings/login are private-chat-only; `/id@BotUsername` works inside groups and forum topics.

## Commands

### Login
`/login [phone]`, `/cancellogin`, `/session <StringSession>`, `/exportsession` (private confirmation), `/logout` (Telegram logout), `/chats` or `/dialogs` (up to 100 dialogs as TXT), `/join <@channel or invite>`.

### Transfer
`/forward`, `/publicforward`, `/save <post links/ranges>`, `/batch <first link> <last ID or link>`, `/grouptransfer <source> [message limit]`, `/bottransfer <@bot> [first ID] [last ID]`, `/status`, `/stop` or `/cancel`, `/resume`, `/discard`.

Send a UTF-8 `.txt` file (<=512 KiB) containing links/ranges. Each job is limited to 10,000 posts. Topic links such as `t.me/c/1234567890/22/55` use **message 55**, not topic 22. Mixed ranges and single links are supported; duplicate link pairs are removed.

The forwarding panel supports job-only promo, caption, filename/caption rename (`old|new`), hyperlink, uploaded thumbnail, destination topic and sender selection. Panel rename does not mutate persistent replacement rules.

### Automatic routes
`/watch <@channel|-100ID|post link> [destination[:topic]]`, `/watchlist`, `/unwatch <route ID|source ID|all>`. Up to 20 routes/user. Cycles within a user's routes are rejected. Cross-user/external automations can still create loops; do not configure them to feed each other.

### Settings and tools
`/settarget` or `/setchannel <destination[:topic]>`, `/id`, `/settings`, `/reset` (keep login or logout), `/setmode <turbo|forward|download|copy>`, `/setworkers <1–5>`, `/setconnections <1–6>`, `/setdelay <0.1–3600>`, `/setpoll <3–3600>`, `/setfilter <all|text>`, `/settypes <comma-separated types|all>`, `/setcaption <text>`, `/clearcaption`, `/replace <old>|<new>`, `/replaces` (delete individual rules), `/clearreplace`, `/ping`, `/stats`, `/myplan`, `/dyno_status`, `/cleanup_ram`.

Types: text, photo, video, document, audio, voice, animation, sticker, poll. Captions: `{original}`, `{caption}`, `{index}`, `{title}`. `/settings` buttons change actual per-user auto-detection, sender and transfer mode. `/myplan` reports configured limits rather than an invented subscription. RAM reported is process peak RAM, not per-user RAM. Cleanup never erases another user's downloads or active transfer files.

### Owner only
`/admin`, `/users` (TXT export), `/broadcast <text>` (actual delivery counts), `/ban <uid>`, `/unban <uid>`, `/approve <uid>`, `/alljobs`, `/access open|whitelist`. Users must register with `/start` before user-specific admin actions. Default access is `open`.

## Operational checks

```bash
python -m py_compile bot.py storage.py transfer.py test_v6.py
python -m unittest test_v6 -v
curl https://YOUR-SERVICE/health
```

Optional `RUN_TRANSPORT_SELFTEST=1`: after startup reuse the bot session to upload a small temporary file to the owner, download it through three parallel senders, verify SHA-256 and delete the Telegram message/local files. Logs state `TRANSPORT_SELFTEST PASS` or `FAIL`. This does not test membership in any user's private channel, video streaming playback or end-to-end live routes. Test those with authorized source/destination accounts.

HTTP `/` and `/health` bind `0.0.0.0:$PORT`. A self-request is best-effort only and cannot guarantee Render free-tier uptime. Session/OTP/password values are not intentionally logged. Limits are configurable in `.env.example`; consider abuse/resource controls before promoting an open deployment publicly.
