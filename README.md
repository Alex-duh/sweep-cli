<p align="center">
  <img src="logo.png" alt="Sweep" width="88" />
</p>

<h1 align="center">Sweep</h1>

<p align="center">
  A tiny Python tool that clears college-recruitment spam out of your Gmail.<br />
  <strong>Archives only — it never deletes anything.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: GPL-3.0" src="https://img.shields.io/badge/License-GPLv3-blue.svg" /></a>
</p>

<p align="center">
  <a href="https://trysweep.vercel.app"><b>🌐&nbsp; Prefer no setup? Try the hosted web app&nbsp; →</b></a>
</p>

---

If you're a student, your inbox is probably buried under "Students like you are discovering State University!" emails. Sweep finds those and archives them — they leave your inbox but stay in **All Mail**, fully searchable, so nothing is ever lost.

It runs entirely on your own computer, against your own Gmail, using your own Google credentials. Nothing is uploaded anywhere.

## What it does

- **Scans** your most recent inbox emails.
- **Classifies** each one with a set of plain, readable rules (no AI, no cloud service). Real emails — acceptances, decisions, personal mail — are left alone.
- **Archives** the recruitment spam by removing its `Inbox` label. It is **never deleted** and can be found anytime in All Mail.
- **Dry run by default.** It shows you what it *would* do and changes nothing until you say yes.

## Safety

- ✅ **Archive only.** Sweep uses Gmail's "modify labels" permission. It has **no ability to delete email** — that permission is never requested.
- ✅ **You confirm first.** The default is a dry run followed by a `y/N` prompt.
- ✅ **Local only.** Your emails and access token never leave your machine.
- ✅ **Whitelist.** Add any sender you always want to keep (see below).

---

## Setup

You'll need **Python 3.9+** and a free Google Cloud project (so the app can talk to *your* Gmail). This takes about 5 minutes and only has to be done once.

### 1. Get the code

```bash
git clone https://github.com/YOUR-USERNAME/sweep-cli.git
cd sweep-cli
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create your Google OAuth credentials

Because Sweep touches your Gmail, Google requires you to authorize it. You'll make your own private credentials — you are the only user, so there's no approval or review needed.

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and sign in.
2. **Create a project:** click the project dropdown (top-left) → **New Project** → name it `Sweep` → **Create**. Make sure it's selected afterward.
3. **Enable the Gmail API:** go to **APIs & Services → Library**, search **Gmail API**, open it, and click **Enable**.
4. **Configure the consent screen:** go to **APIs & Services → OAuth consent screen**.
   - Choose **External** → **Create**.
   - Fill in an **App name** (e.g. `Sweep`), your email for **User support email**, and your email under **Developer contact information**. Click **Save and Continue**.
   - On **Scopes**, just click **Save and Continue** (Sweep requests what it needs at runtime).
   - On **Test users**, click **Add Users** and add **your own Gmail address**. Save and Continue.
   - Leave the **Publishing status** as **Testing**. That's all you need for personal use.
5. **Create the credentials:** go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - **Application type:** **Desktop app**.
   - Give it any name → **Create**.
   - Click **Download JSON** in the popup.
6. **Install the file:** rename the downloaded file to exactly **`credentials.json`** and put it in the `sweep-cli` folder (next to `sweep.py`).

### 3. First run & sign-in

```bash
python sweep.py
```

Your browser will open and ask you to sign in — **use the same Google account you added as a test user**. You'll likely see a **"Google hasn't verified this app"** screen; this is expected because it's *your own* private app. Click **Advanced → Go to Sweep (unsafe)** and continue — it's safe, it's yours. Approve access, and Sweep saves a `token.json` so you won't have to log in again.

---

## Usage

Run it with no arguments for the friendly interactive mode:

```bash
python sweep.py
```

It asks how many recent emails to scan, does a **dry run**, shows you exactly what it found, and then asks before archiving anything.

Prefer flags? These all work too:

| Command | What it does |
| --- | --- |
| `python sweep.py` | Interactive: pick a size, dry run, confirm before archiving |
| `python sweep.py --max 250` | Dry run over your 250 most recent emails — changes nothing |
| `python sweep.py --confirm` | Dry run, then a single `y/N` prompt to archive |
| `python sweep.py --archive` | Scan and archive immediately (no prompt) |
| `python sweep.py --archive --unsubscribe` | Also attempt to unsubscribe from what it archives |
| `python sweep.py --debug` | Show why each email was or wasn't matched |

Sweep only ever looks at your **most recent** emails (not the "next" batch each time). Archived mail leaves your inbox, so running again moves on to what's left — and to clear a large backlog in one pass, use a bigger `--max`.

## Keeping certain senders (whitelist)

To make sure some senders are *never* archived, copy the template and edit it:

```bash
cp whitelist.example.txt whitelist.txt
```

Add one email address per line. Anything from those senders is skipped, no matter what. `whitelist.txt` stays on your machine (it's git-ignored).

## How classification works

Sweep runs two plain-English rule tiers, stopping as soon as one decides:

1. **Definite keep** — strong personal signals (an acceptance, a decision, "congratulations") → always kept.
2. **Definite recruitment** — mass-outreach signals ("students like you", "visit our campus", "start your application", known bulk-mail senders) → archived.

Anything the rules can't confidently place **defaults to keep** — Sweep would rather leave one spam email than archive one real one. It's all in [`classify.py`](classify.py), written to be read.

## Privacy

Everything runs locally. Your emails are read only to classify them and are never stored or sent anywhere except Google's own Gmail API. The optional `sweep_log.json` records message IDs and counts only — never the content, subject, or sender of your mail. Your `token.json` lives only on your computer; delete it (or revoke access in your [Google Account](https://myaccount.google.com/permissions)) anytime.

## License

[GPL-3.0](LICENSE). You're free to use, modify, and share Sweep, but any distributed version or derivative must also stay open source under the GPL.
