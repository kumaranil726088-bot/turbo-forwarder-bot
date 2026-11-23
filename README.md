# Turbo Forwarder V6.2 — Saved Sessions & Resilient Auto-Forwarding

Telegram bot: **@TurboForwarderPro_Bot**. Open access is the default: `OWNER_ID` controls administration, not ordinary forwarding.


## V6.2: saved login recovery and automatic forwarding

- `/login` is idempotent: an already-valid account is reused, not sent through another OTP flow. Private commands, destination checks and watch creation automatically reconnect the requesting user's saved session. A lifecycle lock deduplicates concurrent reconnect attempts.
- On Render, an explicit recovery handoff delay (default 45s, configurable) starts after HTTP binds, so the old deployment has time to retire before the new process reuses account auth keys. This reduces overlapping-MTProto-session revocation risk, but is not horizontal-scaling support. `/health` shows `session_recovery_ready` during this window.
- A 15-second session monitor retries saved account connections with bounded backoff. Temporary transport/capacity failures keep the saved session and do not ask for OTP. Telegram expiry/revocation, explicit `/logout`, account deactivation or invalidated authorization still require fresh login; no application can promise an irrevocable Telegram session.
- A dialog-cache warm-up failure does not discard an authenticated account. Recovering a session from inside a job no longer cancels that calling job.
- Private watches retain live events + polling, original media extraction where Telegram allows it, albums, metadata and thumbnails. Failure retries use bounded backoff and honor FloodWait. Route errors remain visible and do not skip the failed cursor.
- Original-media downloads use atomic `.partial` files, bounded network retries and source-message refresh on expired file references. A failed parallel preallocation is deleted before standard fallback, so an empty/zero-filled partial file cannot be mistaken for a complete download. No transcoding or permission bypass is performed.
- Watch cursors now advance through each message instead of jumping to the maximum album ID, avoiding skipped interleaved posts. A bounded recent-ID ledger suppresses already acknowledged album members, including after checkpoint recovery. This is not an exact-once guarantee: Telegram send and database commits are still separate operations.
- `/watchlist` adds Retry now. Owner panel adds saved-session health, retry saved connection, remote-storage health and manual Sync checkpoint. No OTP/password/session-secret view is exposed.
- `/support` and error/main-menu buttons contact **@SyntaxError_2**. Users are told not to share OTPs, passwords, session strings or encryption keys.

### Free encrypted remote recovery (optional configuration)

For this small deployment, a **separate private GitHub repository** is used for encrypted logical snapshots, accessed at runtime by a **single-repository writable SSH deploy key** with pinned GitHub host keys. The runtime does not need a general account PAT. `SESSION_KEY` stays in Render's environment, not in either repository. The public bot-code repository never receives user state.

`remote_state.py` restores the encrypted database checkpoint **before accepting Telegram requests**, then claims a single-writer checkpoint. Missing/corrupt/unreadable configured storage fails startup instead of silently creating a blank account database. Initialize the repository first with a validated existing backup using `seed_state.py --backup path.tbackup`; only choose `--new-empty` if an explicitly fresh setup is acceptable. Old local ephemeral state is not magically migrated.

- New login/import/logout requests attempt an immediate checkpoint. Important settings/route/access changes also request sync. If sync fails, the active local account remains usable, but the owner is warned and new state is **not yet durable**. Do not restart until sync is healthy.
- Routine job progress, watch cursors and statistics checkpoint every `STATE_SYNC_SECONDS` (default **300 seconds**); latest changes can be lost/replayed after an abrupt crash. Shutdown flush is best effort.
- No-change snapshots are skipped. The remote branch is a rolling root commit, atomically updated with an expected-HEAD lease; an older instance cannot overwrite a newer writer. GitHub may retain unreachable older objects according to its policies; this is not a secure-erasure promise.
- `/health` reports `persistence: encrypted-checkpoints`, checkpoint health and age when enabled. The owner panel shows remote failures. A stale-writer conflict is not automatically overwritten: stop duplicate instances and restart the intended sole writer. Only one serving instance per session store is supported.
- This is a low-volume recovery mechanism, **not** an unlimited/free transactional database, not an uptime solution, and not suitable for high write rates or large-scale bot hosting. GitHub quotas/terms, network outages, Render sleep, Telegram limits and the 10-MiB snapshot cap apply. Use a managed transactional database/persistent server at scale.
- Media/custom-thumbnail files are not included. Manual `/backup` remains an independent recovery fallback. Keep both repositories private/public as intended: the state repository must remain **PRIVATE**. Losing/changing `SESSION_KEY` makes saved snapshots unreadable.

Tests: `python -m unittest test_v6 test_ui test_resilience -v` — **98 tests** including real local-Git subprocess recovery, stale-writer fencing, session recovery, permission gates and watch regressions. A separate real private-GitHub verification branch was also used to test encrypted write → fresh-store recovery and was removed afterwards. These tests do not claim a live end-to-end test of users' private channels.

## Retained V6.1 interface and owner controls

- `/help` and `/start` now show a spaced quick-start page and category navigation: Login, Transfer, Jobs, Auto-Forward, Settings, Captions, Tools, and owner-only Admin guide. Commands have individual descriptions/examples, not dense slash-separated paragraphs. Plain text avoids escaped HTML artifacts.
- Telegram's slash menu lists all ordinary commands in task order. An owner-specific scope adds owner commands first. Owner-only help/navigation remains hidden from ordinary users.
- `/admin` is a real interactive control centre: paginated users, numeric user search, per-user details, ban/unban, whitelist approval/revocation, job inspection/stop, all users' route status/errors, pause/resume/delete, access mode and broadcast preview. Sensitive/destructive button actions require a short-lived, single-use confirmation; navigating away invalidates old confirmations.
- Admin moderation never exports another user's session or exposes OTP/passwords. It does not permit arbitrary actions as another Telegram account. Route resume requires that user to be logged in and allowed. Whitelist changes stop running public-source jobs too, not just jobs with connected user sessions.
- Live route lists are paginated. Very long tool output is split safely within Telegram's UTF-16 message limit.
- `/backup` sends an authenticated Fernet-encrypted `.tbackup` logical database snapshot to the owner DM. Contains registered users, encrypted sessions, settings, routes and checkpoints. The same unchanged `SESSION_KEY` and owner ID are required for restore. Backups do **not** include media/custom-thumbnail files. Maximum backup size: 10 MiB.
- `/restore` must reply to that backup file. It validates encryption, owner, schema/rows and session decryption, previews counts, then asks confirmation. Replacement is an atomic DB transaction; active runtime transfers/sessions are disconnected first. Existing newer DB state is overwritten. Restore is blocked during a broadcast.
- `/reconnect` lets each user reconnect **only their own** restored saved session without a fresh OTP where Telegram still accepts it. Otherwise use `/login`.
- The `/backup` document export is owner-triggered/manual; V6.2 remote checkpoints above are separate. Save a backup before deploys. Free Render sleep and ephemeral filesystem limits still apply. Creating a website cannot remove hosting/storage quotas. An external DB needs separately supplied account/connection credentials; none was provisioned by this change.

The original UI suite remains included in the full regression command above. These do not certify end-to-end private/restricted-channel transfers or Telegram permissions. V6 live transport verification is documented separately in the workspace research notes.

## Architecture

- `bot.py`: command/callback router, private-chat security gates, isolated logins/jobs/panels, ordered prefetch pipeline, event-driven live routes and catch-up, real forum creation, HTTP health service.
- `storage.py`: parameterized SQLite tables `users`, `user_settings(uid,key)`, `global_settings`; legacy V5 settings migrate to the owner once. Telegram sessions are Fernet-encrypted using `SESSION_KEY`. Plaintext legacy rows are securely removed after migration.
- `transfer.py`: public/private/topic/range link parsing, album helpers, bounded parallel MTProto download/upload using 512-KiB chunks, separate MTProto senders, cross-DC authorization export/import, normal Telethon fallback.
- `help_ui.py`: paginated command guide and full slash menu definitions.
- `control_ui.py`: owner moderation interface, confirmations and encrypted backup/restore flows.
- `sessions.py`: per-user lifecycle locks, transport reconnect and revoked-vs-transient state.
- `remote_state.py`: encrypted remote checkpoints with atomic writer fencing.
- `seed_state.py`: operator-only initial backup migration helper.
- `test_v6.py`, `test_ui.py`, `test_resilience.py`: regression suites.

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

**Render free web-service filesystems are ephemeral.** Without a configured remote checkpoint or persistent disk/database, redeploy/restart can erase local settings, sessions, routes and checkpoints. Encryption alone does not provide persistence. V6.2 encrypted Git checkpoints restore the latest successfully synced snapshot; the local disk remains ephemeral and newer unsynced changes can be lost. Unconfigured deployments still report `persistence: ephemeral`.

As an alternative to encrypted remote checkpoints, configure a Render persistent disk (requires a suitable paid service plan) and point `DB_PATH` and `DOWNLOAD_DIR` at its mount, e.g. `/var/data/botdata.db` and `/var/data/downloads`; set `PERSISTENT_STORAGE=1` only after doing this. The application does not purchase or upgrade hosting automatically. Existing databases migrate only if available at `DB_PATH`; it cannot recover a database already lost from an earlier ephemeral instance.

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
`/login [phone]`, `/reconnect`, `/cancellogin`, `/session <StringSession>`, `/exportsession` (private confirmation), `/logout` (Telegram logout), `/chats` or `/dialogs` (up to 100 dialogs as TXT), `/join <@channel or invite>`.

### Transfer
`/forward`, `/publicforward`, `/save <post links/ranges>`, `/batch <first link> <last ID or link>`, `/grouptransfer <source> [message limit]`, `/bottransfer <@bot> [first ID] [last ID]`, `/status`, `/stop` or `/cancel`, `/resume`, `/discard`.

Send a UTF-8 `.txt` file (<=512 KiB) containing links/ranges. Each job is limited to 10,000 posts. Topic links such as `t.me/c/1234567890/22/55` use **message 55**, not topic 22. Mixed ranges and single links are supported; duplicate link pairs are removed.

The forwarding panel supports job-only promo, caption, filename/caption rename (`old|new`), hyperlink, uploaded thumbnail, destination topic and sender selection. Panel rename does not mutate persistent replacement rules.

### Automatic routes
`/watch <@channel|-100ID|post link> [destination[:topic]]`, `/watchlist`, `/unwatch <route ID|source ID|all>`. Up to 20 routes/user. Cycles within a user's routes are rejected. Cross-user/external automations can still create loops; do not configure them to feed each other.

### Settings and tools
`/settarget` or `/setchannel <destination[:topic]>`, `/id`, `/settings`, `/reset` (keep login or logout), `/setmode <turbo|forward|download|copy>`, `/setworkers <1–5>`, `/setconnections <1–6>`, `/setdelay <0.1–3600>`, `/setpoll <3–3600>`, `/setfilter <all|text>`, `/settypes <comma-separated types|all>`, `/setcaption <text>`, `/clearcaption`, `/replace <old>|<new>`, `/replaces` (delete individual rules), `/clearreplace`, `/support` (contact @SyntaxError_2), `/ping`, `/stats`, `/myplan`, `/dyno_status`, `/cleanup_ram`.

Types: text, photo, video, document, audio, voice, animation, sticker, poll. Captions: `{original}`, `{caption}`, `{index}`, `{title}`. `/settings` buttons change actual per-user auto-detection, sender and transfer mode. `/myplan` reports configured limits rather than an invented subscription. RAM reported is process peak RAM, not per-user RAM. Cleanup never erases another user's downloads or active transfer files.

### Owner only
`/admin`, `/users` (paginated management; TXT export button), `/broadcast <text>` (actual delivery counts), `/ban <uid>`, `/unban <uid>`, `/approve <uid>`, `/alljobs`, `/access open|whitelist`, `/backup`, `/restore`. Users must register with `/start` before user-specific admin actions. Default access is `open`.

## Operational checks

```bash
python -m py_compile *.py
python -m unittest test_v6 test_ui test_resilience -v
curl https://YOUR-SERVICE/health
```

Optional `RUN_TRANSPORT_SELFTEST=1`: after startup reuse the bot session to upload a small temporary file to the owner, download it through three parallel senders, verify SHA-256 and delete the Telegram message/local files. Logs state `TRANSPORT_SELFTEST PASS` or `FAIL`. This does not test membership in any user's private channel, video streaming playback or end-to-end live routes. Test those with authorized source/destination accounts.

HTTP `/` and `/health` bind `0.0.0.0:$PORT`. A self-request is best-effort only and cannot guarantee Render free-tier uptime. Session/OTP/password values are not intentionally logged. Limits are configurable in `.env.example`; consider abuse/resource controls before promoting an open deployment publicly.

## V6.3 — ordered queue, poll recovery and visible I/O

- Polls use native forwarding when eligible and explicitly requested; otherwise a
  clearly labelled, non-interactive text archive preserves question/options and
  available vote totals. Hidden quiz answers/votes are not cloned. Long archives
  are split by Telegram UTF-16 message limits.
- `/resume` keeps the paused job's destination/customizations and applies changed
  sender/mode/workers/connections/delay/filter/type settings. Legacy checkpoints
  use the current transport/filter settings. `/resume latest` explicitly uses all
  current settings, including destination and caption.
- `/failed` exports your latest 500 recorded content failures. `/retryfailed`
  matches current destination, topic **and sender**; filtered/missing items do not
  clear a failure record. Global permission/auth/network/disk failures still pause
  without advancing the failed cursor. Sending and checkpointing are not atomic.
- Ordered prefetch reserves bounded temporary disk outside transfer slots. A
  cached/native candidate holds later admission until its extraction fallback is
  resolved, preventing later downloads from consuming its required disk. Workers
  pipeline downloads with sending; they do not reorder destination posts.
- Every five seconds the status card shows cumulative sent, this-run sent,
  skipped/failed units, archived polls, native media copies, and download/upload
  callback bytes. Retry traffic is counted where callbacks report it. These are
  payload callback counters, not a network-interface benchmark; average I/O sums
  download + upload over this run's elapsed time. Albums contain multiple messages.
- `/fast` requires explicit User/Bot sender selection; it is not an automatic
  permission bypass. User sender + private default destination means **Saved
  Messages**, not this bot DM. Source/destination access and Telegram restrictions
  still apply. Restricted, renamed or custom-thumbnail files may need full I/O.
- Parallel MTProto connections initialize concurrently with all-started sender
  cleanup on failure/cancellation. FloodWait is honored. No free-host throughput,
  uptime or “2,000 × 900 MB in two hours” guarantee.

Validation: 121 automated tests, including 23 queue/connection regressions; live
bot-account poll archive + following-message check; 3-connection upload/download
SHA256 check; 2-sender cross-DC authorization check. Temporary live test messages
were deleted. No live private-user account or 900 MB throughput certification.

## V6.4 — warm transports and path diagnostics

- `/why <one post link>` performs a read-only metadata/access preflight against
  your current settings/destination. It downloads no file and sends no copied
  content. It explains same-session native candidates, account mismatch, source
  protection, download mode, rename/custom-thumbnail requirements. A preflight is
  not an actual send test and cannot guarantee Telegram acceptance.
- Native expired/invalid media references get one metadata refresh + native retry
  before extraction fallback, avoiding a needless full reupload on that error.
  Disappeared content is recorded; newly protected messages are not natively reused.
- Healthy MTProto sender sets can be reused. The **global** cache has at most
  GLOBAL_TRANSFERS entries (default 2), each at most 6 sockets. Leases are exclusive,
  keyed by exact Telegram client instance, data center and connection count. No
  cross-account access hashes/keys, copied messages, or session secrets are saved
  in database settings by this cache. Failed/cancelled operations discard their
  connection entry. Logout/replacement retires that account's entries; shutdown
  closes the cache. Idle entries expire on next acquisition after 30 seconds.
- For a new remote DC pool, one export/import pair is serialized using Telethon's
  borrowed-sender lock. Followers connect concurrently using a copy of that DC's
  authorized key rather than racing several one-use authorization imports.
- Per-job/watch-drain metadata cache coalesces source lookups, bounded to 128
  source/client pairs and 30-second TTL. Telegram enforces permissions on actual
  reads/sends. Runtime transport/cache objects are never checkpointed.
- Existing sender/destination/settings remain unchanged. No silent switch from
  Bot DM to User Saved Messages. `/fast` is still an explicit choice. Authorized
  protected-source extraction still needs payload download/upload, and any denial
  remains visible. Existing default extraction cap remains 1900 MiB; native copies
  do not use local download capacity. This is NOT a promise of all 2GB files being
  reuploaded on the existing free host.

Validation: 147 tests pass. Live sandbox bot-account checks: native media copy
without invoking the downloader; two pooled uploads/downloads with SHA256 match;
three-sender DC2 and DC4 cold/warm initialization. An initial multi-export remote
DC test failed with AuthBytesInvalidError; the single-export initialization fix
passed subsequent DC2/DC4 checks. Small ~1MiB upload sample was 2.87s cold / 1.58s
warm. That sample isolates setup overhead and is NOT a 2GB, private-user-account,
production, or sustained-throughput benchmark. All three temporary test messages
were removed. No rate-limit bypass, unlimited-storage, uptime or accuracy guarantee.

## V6.5 — destination-aware Auto sender (default)

The previous Bot default caused unnecessary source-account → bot-account reuploads
when the intended destination was a channel. Default `sender=Auto` now uses the
requester's own authorized account for channel/group destinations, preserving the
exact configured channel/topic. Own private/default destination stays **this bot
DM**; only explicit `User` + own private destination means Saved Messages.
Existing explicitly stored Bot/User choices remain intact. Private sources still
require the requester's own session/membership. Every channel destination is
permission-checked; Auto never falls back to shared bot rights on denial.

- `/setsender auto` enables the policy; explicit overrides `bot` / `user` remain.
- `/setchannel @destination` (or `-100...[:topic]`) verifies and saves the target,
  including the paused batch target, without moving the cursor. Already sent
  posts are not moved/replayed. Active batches must be stopped first. A changed
  destination clears any old destination-specific forum-topic map visibly.
- `/resume` then continues remaining posts; other customization/filter settings
  remain unchanged. Existing routes retain their own destination, not the newly
  selected global target. New automatic routes: `/watch <source>`; these start
  with NEW posts. Existing history uses a batch, not an implicit full-history scan.
- Progress cards show destination and selected → effective sender so a private
  bot-DM default cannot be mistaken for a configured channel.
- `/why` reports selected → effective sender. Protection is diagnosed before
  sender mismatch, so a protected source is not advertised as natively copyable
  merely by changing accounts. Authorized protected extraction still consumes
  download/upload bandwidth; no speed/protection/permission bypass guarantee.
- Failed-item records match the effective Bot/User sender, not policy label Auto.
- Runtime permission-check races are rechecked before modifying paused settings.

Validation:166 automated tests pass (19 new Auto-sender regressions). Default bot
DM preservation, explicit Bot/User overrides, channel rights denial, >local-cap
native candidates without downloader calls, checkpoint retarget/cursor preservation,
source/destination same-client planning, and per-user isolation tested with mocks.
No sandbox login using private saved-user sessions and no live private-user channel
send performed for this release. Production state is restored, never reseeded.

V6.5.1 preservation guard: a new deployment default is not treated as an explicit
user sender edit when an old paused job has a distinct panel-selected sender.
This preserves historical explicit User/Saved Messages panel jobs on ordinary
/resume. An explicitly stored /setsender auto still overrides that panel choice;
/resume latest remains the deliberate all-current-settings option.168 tests pass.
