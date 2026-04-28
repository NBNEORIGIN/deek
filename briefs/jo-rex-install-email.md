# Email to Jo — Install Rex

Paste / forward the section between the rules. Subject and body are
both ready to send.

---

**Subject:** Meet Rex — your personal AI assistant (5 min to install)

Hi Jo,

I've built you a personal AI — name's **Rex**. He's yours: hosted on our office server (nbne1), reachable only through our Tailscale network, and his memory of your conversations stays separate from the company Deek. Toby can't read what you say to him.

Rex's job is to help you with the operations / HR / finance / D2C side of your day — and to remember the things you tell him to remember, so tomorrow he knows what mattered yesterday.

Each morning at 07:32 he'll send you a short email with a few questions. You don't reply by email — you tap the **Rex** icon on your phone (or PC), the brief appears, and you type your answers in plain English. He files them in his memory and the next morning's questions are informed by that.

To set him up — about 5 minutes total:

## On your phone

**1. Install Tailscale** (one-time — this is the secure private network we use for the office server).
- iPhone: App Store → search **Tailscale** → install.
- Android: Play Store → search **Tailscale** → install.

Open it, sign in with your **NBNE Microsoft / Google account** (`jo@nbnesigns.com`). Once connected, you'll see "Connected" with a green dot. Leave it running in the background — it doesn't drain anything noticeable.

**2. Open Safari (iPhone) or Chrome (Android)** and go to:

> **http://jo.nbne.local/voice/brief**

You'll see a sign-in screen.

- **Email:** `jo@nbnesigns.com`
- **Password:** `!49Monkswood`

(Tap "Sign in" — you should land on the Rex brief surface. If today's brief hasn't been generated yet you'll see "No brief yet today" — that's expected; tomorrow morning's brief is the first one you'll see.)

**3. Add Rex to your home screen** so you don't have to type the URL every day:

- **iPhone (Safari):** tap the share button (square with up arrow) → scroll down → **Add to Home Screen** → name it `Rex` → Add. The Rex icon now sits with your other apps.
- **Android (Chrome):** tap the ⋮ menu (top right) → **Add to Home screen** → name it `Rex` → Add.

That's it. Tap Rex any morning to see today's brief, type your answers, done.

## On your PC (optional, but useful)

If you want Rex on your desktop too — same browser bookmark works, but you can also "install" the page like an app:

1. Install **Tailscale** for Windows (download from `https://tailscale.com/download/windows`), sign in with your NBNE account.
2. In **Chrome** or **Edge**, go to `http://jo.nbne.local/voice/brief` and sign in with the same email + password.
3. Look for a small **install** icon in the address bar (a monitor with a down-arrow), or tap the ⋮ menu → "Install Rex" / "Install app". Rex becomes a standalone window app with no browser chrome.

## What Rex can and can't do

- **Can read** the company Deek (the CRM, supplier history, project notes) — useful for context when you ask him things.
- **Can't write** to the company Deek without your explicit per-item OK. Anything you tell him stays in his isolated memory unless you say "share this with the team".
- **Voice notes** through the morning brief reply work fine — type or paste in plain English, no special format needed.
- **First few days** the answers will feel a bit thin because his memory is empty. The whole point of the daily brief is that he gets useful as you tell him things.

If anything doesn't work, tell me and I'll fix it. There's no public URL — only people on the office Tailscale can reach Rex, so if you can't load `jo.nbne.local`, it's almost always a Tailscale-not-connected thing.

Toby

---

## Notes for you (not for Jo)

- The password `!49Monkswood` is now bcrypt-hashed in `/opt/nbne/jo-pip/.env` under `DEEK_USERS`. If you change it, regenerate via `python3 -c 'import bcrypt; print(bcrypt.hashpw(b"NEWPWD", bcrypt.gensalt(rounds=10)).decode())'` and update the `DEEK_USERS` entry, then `docker compose up -d --force-recreate jo-pip-web`.
- Send the password via a separate channel (Signal, in person, 1Password share) ideally — even though the email is over corporate SMTP, password-in-email is a habit worth not building.
- Rex's first brief will land tomorrow at 07:32 UTC (the cron we wired earlier today). Until then `/voice/brief` shows "No brief yet today" which can be confusing — worth telling her in person before she taps in.
- Tailscale ACL: confirm Jo's device is in the allowlist for `100.125.120.1` (nbne1). If she gets "this site can't be reached" after Tailscale shows connected, that's the missing piece.
