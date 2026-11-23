"""Readable, paginated Telegram command reference; plain text avoids HTML artifacts."""
from telethon import Button

PAGES = {
'home': ('⚡ TURBO FORWARDER • COMMAND CENTRE', '''Har user ka login, destination, job aur live routes alag hain.

🚀 QUICK START

1. /login
   Apna Telegram account connect karein.

2. /setchannel @yourchannel
   Apna destination verify karke save karein.

3. /forward
   First/end link select karke transfer shuru karein.

4. /watch @source @destination
   Naye posts ka automatic transfer enable karein.

Neeche category select karein. Har page par commands aur examples milenge.

🆘 Problem? /support → @SyntaxError_2. OTP/password/session key kabhi share na karein.

🔒 Login/settings private chat mein use karein. /id groups aur topics mein bhi kaam karta hai.'''),
'login': ('🔐 LOGIN & ACCOUNT', '''/login
   Pehli baar phone + inline OTP keypad se connect karein. Already-valid saved login ho to naya OTP nahi maanga jaata. Temporary disconnect automatic retry hota hai; revoked/expired Telegram session mein fresh login zaroori hai.

/reconnect
   Backup restore ke baad apni saved session ko bina naya OTP maange reconnect karne ki koshish karein. Expired ho to /login.

/cancellogin
   Chal raha login cancel karein.

/logout
   Apna Telegram session logout karein. Live routes reconnect hone tak nahi chalenge.

/session STRING_SESSION
   Sirf apne account ki existing session import karein.

/exportsession
   Confirmation ke baad apni sensitive session file export karein.

/chats  •  /dialogs
   Apne pehle 100 chats ki ID list TXT mein paayein.

/join @channel
   Public channel join karein; private invite link bhi supported hai.

⚠ Account session full account access deti hai. Operator par trust ho tabhi login karein. OTP/password kisi admin ko forward na karein.'''),
 'transfer': ('📤 MANUAL TRANSFER', '''/forward
   Private source ke liye login → first link → end link → settings → Start.

/publicforward
   Public-source forwarding panel kholein. Private access ke liye login zaroori hai.

/save https://t.me/channel/10-20
   Links, ranges ya mixed post links transfer karein.

/batch https://t.me/channel/10 50
   First link se last ID tak transfer karein; last post link bhi de sakte hain.

/grouptransfer @source 500
   Latest 500 messages copy karein. Forum→forum mein real topics match/create hote hain.

/bottransfer @sourcebot 1 100
   Apne logged-in account ki bot conversation se IDs 1–100 transfer karein.

📎 UTF-8 TXT file bhejein: har line mein link/range. Maximum 512 KiB; maximum 10,000 posts/job.

🗳 Polls: native-forward eligible ho to original forward; otherwise clearly labelled text archive. Votes/hidden quiz answers clone nahi hote. Ek poll video queue nahi rokta.

✨ Panel options
   Promo • Caption • Rename • Hyperlink • Uploaded thumbnail • Topic • Sender.

Rename: old|new, ya Lesson_{index}. Caption: {original}, {index}, {title}.'''),
'jobs': ('⏯ JOB & RESUME CONTROLS', '''/status
   Apne current job, login aur resume status dekhein.

/stop
   Sirf apna active transfer stop karein; confirmed checkpoint rakhein.

/cancel
   Apna job, panel aur login flow cancel karein. Live routes alag hain.

/resume
   Last confirmed checkpoint se resume karein; changed transport/filter settings apply hote hain. Job destination/custom captions retained.

/resume latest
   ALL current settings use karein, including current destination/caption.

/failed
   Recorded content failures ki TXT list. Maximum latest 500 retained.

/retryfailed
   Current destination ke failed items retry karein.

/discard
   Stopped job ka resume checkpoint delete karein.

⚠ /stop live routes ko pause nahi karta. Uske liye /watchlist kholein.

Crash ke waqt send aur checkpoint ke beech gap ho to resume par duplicate ho sakta hai.'''),
'watch': ('🔴 AUTOMATIC FORWARDING', '''/watch @source @destination
   Naye posts ke liye live route add karein.

/watch -1001234567890 @destination:42
   Private source ID → destination forum topic 42.

/watch https://t.me/c/1234567890/55
   Post link se source detect karein; saved destination use hoga.

/watchlist
   Har route ka cursor/error dekhein; Pause, Resume, Delete buttons use karein.

/unwatch ROUTE_ID
   Sirf selected route delete karein.

/unwatch all
   Apne saare routes delete karein.

⚙ Kaise chalta hai
   Logged-in account ka NewMessage event + polling catch-up. Albums ke liye 0.7s settling. Failed post ka cursor skip nahi hota.

Naya route latest post se start hota hai. Pause/resume stored cursor se backlog catch-up karta hai.

🔒 Private source mein apni membership aur copy karne ki permission chahiye. Restricted media sirf tab extract/re-upload hota hai jab Telegram download allow kare. No permission-bypass guarantee.'''),
'settings': ('⚙ DESTINATION & PERFORMANCE', '''/settings
   Apni current settings dekhein; auto-detect, sender aur mode buttons se badlein.

/setchannel @destination
   Destination save karein. Alias: /settarget.

/setchannel -1001234567890:42
   Destination forum topic 42 save karein.

/id
   Current chat/message/topic ID dekhein. Groups mein /id@BotUsername bhi supported hai.

/why <post link>
   Read-only transfer-path check: source/sender mismatch, protection, rename/thumb, current destination. No file downloaded/sent.

/fast
   User/Bot sender profile choose karein. User sender + own private default destination means Saved Messages, not this bot DM. Restricted content still requires permitted extraction.

/setmode turbo
   Options: turbo, copy, download, forward. Native forwarding same session aur allowed source par hi hoti hai.

/setworkers 2
   Preparation workers: 1–5.

/setconnections 4
   Parallel MTProto connections/file: 1–6.

/setdelay 0.1
   Sends ke beech minimum delay: 0.1–3600 seconds.

/setpoll 10
   Live-route catch-up interval: 3–3600 seconds.

/setfilter all
   Options: all, text.

/settypes photo,video,document
   Types: text, photo, video, document, audio, voice, animation, sticker, poll. Sab ke liye: /settypes all.

⚠ Zyada workers hamesha faster nahi hote; server aur Telegram rate limits apply hoti hain.'''),
'captions': ('✏ CAPTIONS & REPLACEMENTS', '''/setcaption Lesson {index}\n{original}
   Custom caption template save karein.

Available placeholders
   {original} / {caption} — source text
   {index} — job index
   {title} — source filename

/clearcaption
   Custom caption hataayein; source caption use hoga.

/replace old|new
   Apna persistent text replacement add karein.

/replaces
   Rules dekhein aur individual delete buttons use karein.

/clearreplace
   Apne saare replacement rules clear karein.

Panel-only rename old|new caption aur filename par apply hota hai; persistent replacement list nahi badalta.

Original unmodified captions ke entities preserve hote hain. Customized text plain text hai; panel Hyperlink option clickable links banata hai.'''),
 'tools': ('🧰 TOOLS & FREE RECOVERY', '''/ping
   Telegram request round-trip time check karein.

/stats
   Apne sent messages, job aur live-route counts dekhein.

/myplan
   Configured capacity aur file limits dekhein; invented premium plan nahi.

/dyno_status
   Uptime aur process peak RAM dekhein.

/cleanup_ram
   Apni inactive temporary files clean karein; active files/other users untouched.

/reset
   Apni settings/routes/checkpoint reset karein; login keep/logout choice milegi.

👑 Owner backup controls
/backup — encrypted database backup document.
/restore — reply to backup file, preview + confirmation.

🔐 /status mein storage health dekhein. Encrypted remote checkpoints configured hon to startup saved logins/settings restore karta hai. Login snapshots immediately sync karne ki koshish hoti hai; routine changes periodic hain.

Free Render ka local disk ab bhi ephemeral hai. Git checkpoints transactional database nahi hain; quotas/outages apply. Manual /backup fallback rakhein.

/support
   Bot issues ke liye @SyntaxError_2 se contact karein. OTP/password/session string share na karein.'''),
'admin': ('👑 OWNER COMMAND GUIDE', '''/admin
   Interactive control centre: users, jobs, routes, access, broadcast aur backup.

/users
   Searchable-by-ID user management buttons + paginated list.

/alljobs
   Running jobs inspect karein; per-user stop controls.

/ban USER_ID
   Bot access block karein aur active transfers/session disconnect karein.

/unban USER_ID
   Bot ban hataayein; whitelist approval alag setting hai.

/approve USER_ID
   Whitelist access approve karein.

/access open
   Sab registered users ko access (banned users excluded).

/access whitelist
   Sirf owner aur approved users ko access.

/broadcast MESSAGE
   Registered users ko message bhejein; actual delivery/failure counts paayein.

/backup
   Encrypted DB snapshot ko apne Telegram DM mein save karein.

/restore
   Apne encrypted backup par reply karke validation preview kholein.

Admin panel bot-level moderation ke liye hai. Users ke passwords/OTP/session strings expose nahi karta; user impersonation ya unke account se arbitrary actions nahi deta.''')
}
LABELS = [('login','🔐 Login'),('transfer','📤 Transfer'),('jobs','⏯ Jobs'),('watch','🔴 Auto-Forward'),('settings','⚙ Settings'),('captions','✏ Captions'),('tools','🧰 Tools'),('admin','👑 Owner guide')]

def help_page(page='home',owner=False):
    if page not in PAGES or page=='admin' and not owner:
        page='home'
    title,body=PAGES[page]
    pairs=[(k,label) for k,label in LABELS if owner or k!='admin']
    buttons=[[Button.inline(label,('help_'+key).encode()) for key,label in pairs[i:i+2]] for i in range(0,len(pairs),2)]
    buttons.append([Button.inline('🏠 Command home',b'help_home'),Button.inline('🚀 Main menu',b'nav_home')])
    return title+'\n━━━━━━━━━━━━━━━━━━━━\n\n'+body,buttons

# Telegram slash-menu order: everyday actions first, then settings/account/tools.
SLASH_MENU = [
('start','🏠 Main menu and quick start'),('help','📖 Category-wise command guide'),('support','🆘 Contact @SyntaxError_2 for help'),
('forward','📤 Private transfer settings panel'),('publicforward','🌐 Public-source transfer panel'),
('watchlist','🔴 Manage automatic routes'),('watch','➕ Add a live source/destination route'),
('unwatch','🗑 Remove a live route'),('status','📊 My current transfer status'),('why','🔎 Explain transfer path for a post link'),('fast','⚡ Choose a fast sender profile'),('failed','📄 My recorded failed content items'),('retryfailed','🔄 Retry failures for current destination'),
('stop','⏹ Stop my transfer; keep checkpoint'),('resume','▶ Resume my pending transfer'),
('cancel','✖ Cancel my job or input flow'),('discard','🗑 Delete my resume checkpoint'),
('save','🔗 Transfer post links or ranges'),('batch','📚 Transfer first-to-last message range'),
('grouptransfer','🗂 Transfer group messages and forum topics'),('bottransfer','🤖 Transfer my bot conversation messages'),
('settings','⚙ My settings and real toggles'),('setchannel','🎯 Set destination chat or forum topic'),
('settarget','🎯 Alias for setchannel'),('id','🆔 Show chat/message/topic IDs'),
('setmode','⚡ Select turbo/copy/download/forward'),('setworkers','⚡ Set preparation workers (1–5)'),
('setconnections','⚡ Set transport connections (1–6)'),('setdelay','⏱ Set minimum send delay'),
('setpoll','🔄 Set live catch-up interval'),('setfilter','🔎 Select all or text-only'),
('settypes','🔎 Select media/message types'),('setcaption','✏ Set my caption template'),
('clearcaption','✖ Remove my custom caption'),('replace','🔁 Add old|new text replacement'),
('replaces','📋 View/delete my replacement rules'),('clearreplace','✖ Remove all my replacements'),
('login','🔐 Connect my Telegram account'),('reconnect','🔄 Reconnect my saved session'),('cancellogin','✖ Cancel my account login'),
('logout','🔓 Log out my connected session'),('session','🔐 Import my own session string'),
('exportsession','⚠ Export my private session backup'),('chats','📂 My first 100 chat IDs'),
('dialogs','📂 Alias for chats'),('join','➕ Join channel or invite as my account'),
('ping','📡 Measure Telegram round-trip'),('stats','📊 My transfer statistics'),
('myplan','📏 Actual configured limits'),('dyno_status','🖥 Uptime and process RAM'),
('cleanup_ram','🧹 Clean my inactive temporary files'),('reset','♻ Reset only my settings')]
OWNER_MENU = [('admin','👑 Owner control centre'),('users','👥 Manage registered users'),
('alljobs','⚡ Inspect/stop running jobs'),('broadcast','📣 Send owner announcement'),
('ban','🚫 Block bot access for user'),('unban','✅ Remove user ban'),
('approve','🔑 Approve whitelist user'),('access','🚪 Select open/whitelist mode'),
('backup','💾 Export encrypted database backup'),('restore','♻ Preview/restore encrypted backup')]
