"""
sweep.py — main entry point for Sweep

Usage:
  python sweep.py                        # dry run: shows what would happen, changes nothing
  python sweep.py --archive              # archives recruitment emails for real
  python sweep.py --archive --unsubscribe  # archive + attempt to unsubscribe
  python sweep.py --max 200              # scan more messages (default: 100)
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import unquote

import requests
from tqdm import tqdm #for progress bar
from rich.console import Console # for pretty printing tables and colored text
from rich.table import Table
from rich.prompt import IntPrompt  # friendly numeric prompt for interactive mode

from auth import get_service
from classify import classify


# ---------------------------------------------------------------------------
# Fetching messages from Gmail
# ---------------------------------------------------------------------------

def get_inbox_ids(service, max_results=100):
    """
    Returns a list of {id, threadId} dicts for messages currently in the inbox.
    We only fetch IDs here — full content is fetched one-by-one in get_message().
    """
    response = service.users().messages().list(
        userId="me",
        labelIds=["INBOX"],   # only look at inbox, not all mail
        maxResults=max_results,
    ).execute()
    return response.get("messages", [])


def decode_body(payload):
    """
    Recursively extracts readable text from a Gmail message payload.

    Gmail messages can be 'multipart' (like a ZIP with multiple files inside).
    We prefer plain text over HTML. If only HTML exists, we strip the tags.
    """
    mime = payload.get("mimeType", "")

    # Plain text — ideal, return it directly.
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            # Gmail encodes body data as base64url — we decode it to a string.
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Multipart — the message has several sections (e.g., plain + HTML).
    # First pass: look for a plain text section.
    if "multipart" in mime:
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                return decode_body(part)
        # Second pass: recurse into all parts if no plain text was found.
        for part in payload.get("parts", []):
            text = decode_body(part)
            if text:
                return text

    # HTML fallback — strip tags so keyword matching still works.
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", html)  # replace every tag with a space

    return ""


def parse_headers(header_list):
    """Convert Gmail's [{name, value}, ...] header list to a plain {name: value} dict."""
    return {h["name"]: h["value"] for h in header_list}


def get_message(service, msg_id):
    """
    Fetch one message from Gmail and return a clean dict that classify() can use.
    format="full" gives us headers + decoded body (vs "minimal" which is ID only).
    """
    raw = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    payload = raw.get("payload", {})
    headers = parse_headers(payload.get("headers", []))

    return {
        "id": msg_id,
        "sender":  headers.get("From", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "snippet": raw.get("snippet", ""),   # Gmail's ~100-char preview
        "body":    decode_body(payload),
        "headers": headers,                  # kept so try_unsubscribe() can read them
    }


# ---------------------------------------------------------------------------
# Unsubscribe logic
# ---------------------------------------------------------------------------

def get_unsubscribe_targets(headers):
    """
    Parse the List-Unsubscribe header into its two possible forms.
    Returns (mailto_address_or_None, http_url_or_None).

    Standard header looks like:
      List-Unsubscribe: <mailto:unsub@x.com?subject=unsub>, <https://x.com/unsub?id=1>
    """
    raw = headers.get("List-Unsubscribe", "")

    mailto_match = re.search(r"<mailto:([^>]+)>", raw, re.IGNORECASE)
    http_match   = re.search(r"<(https?://[^>]+)>", raw, re.IGNORECASE)

    mailto  = mailto_match.group(1) if mailto_match else None
    http_url = http_match.group(1)  if http_match   else None

    return mailto, http_url


def try_unsubscribe(service, email):
    """
    Attempt to unsubscribe using the List-Unsubscribe header.
    Tries three methods in order, stopping at the first that works.

    Method 1 — RFC 8058 one-click POST: the modern standard.
      Many email providers (Gmail, Outlook) require senders to support this.
      We POST exactly what the spec says: List-Unsubscribe=One-Click.

    Method 2 — mailto: send an unsubscribe email via the Gmail API.
      Older but very common. We literally email the unsubscribe address.

    Method 3 — HTTP GET: visit the unsubscribe URL.
      Works for simple one-click links. Fails if the page needs a button click,
      but we have no way to detect that without browser automation.

    Returns a string describing what happened (for logging).
    """
    headers  = email.get("headers", {})
    mailto, http_url = get_unsubscribe_targets(headers)

    if not mailto and not http_url:
        return "no List-Unsubscribe header — cannot unsubscribe programmatically"

    # Method 1: RFC 8058 one-click POST (requires List-Unsubscribe-Post header).
    if http_url and "List-Unsubscribe-Post" in headers:
        try:
            resp = requests.post(
                http_url,
                data="List-Unsubscribe=One-Click",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            if resp.ok:
                return f"one-click POST → {http_url[:60]}"
        except requests.RequestException:
            pass  # fall through to next method

    # Method 2: mailto — send an unsubscribe email from your Gmail account.
    if mailto:
        try:
            # mailto can be "address@x.com" or "address@x.com?subject=Unsub"
            if "?" in mailto:
                address, query = mailto.split("?", 1)
                # Parse the subject from the query string, decode %20 etc.
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                subject = unquote(params.get("subject", "Unsubscribe"))
            else:
                address = mailto
                subject = "Unsubscribe"

            msg = MIMEText("")          # empty body — the subject line is the signal
            msg["to"] = address
            msg["subject"] = subject

            # Gmail API expects the message as base64url-encoded RFC 2822 bytes.
            raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me", body={"raw": raw_bytes}
            ).execute()
            return f"sent unsubscribe email → {address}"
        except Exception:
            pass

    # Method 3: HTTP GET — last resort.
    if http_url:
        try:
            resp = requests.get(http_url, timeout=10)
            if resp.ok:
                return f"GET → {http_url[:60]}"
            return f"GET failed (status {resp.status_code}) → {http_url[:60]}"
        except requests.RequestException as e:
            return f"GET error: {e}"

    return "all unsubscribe methods failed"


# ---------------------------------------------------------------------------
# Archiving
# ---------------------------------------------------------------------------

def archive_message(service, msg_id):
    """
    Archive an email by removing the INBOX label.

    This is NOT deletion. The email still exists under All Mail and is fully
    recoverable. Gmail's own Archive button does exactly the same thing.
    """
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["INBOX"]},
    ).execute()


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

LOG_FILE    = "sweep_log.json"
SEEN_ID_FILE = "seen_ids.txt"


def load_seen_ids():
    """
    Returns the set of Gmail message IDs already processed and left in inbox.
    Only non-recruitment decisions are stored — archived emails disappear from
    the inbox naturally, so they never need to be skipped.
    Missing file is fine — returns empty set.
    """
    try:
        with open(SEEN_ID_FILE) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen_ids(new_ids):
    """Appends a batch of new IDs to seen_ids.txt (one per line)."""
    with open(SEEN_ID_FILE, "a") as f:
        for msg_id in new_ids:
            f.write(msg_id + "\n")


def determine_tier(reason: str) -> str:
    """
    Decide from the reason string whether a rule positively decided this email
    ("rule") or it fell through to the safe default keep ("default").
    Used for the stats summary and log entries.
    """
    return "rule" if reason.startswith("rule:") or reason.startswith("no college") else "default"


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

def load_whitelist(path="whitelist.txt"):
    """
    Reads whitelist.txt and returns a set of lowercase email addresses.
    Lines starting with # are comments and are ignored.
    Missing file is fine — returns an empty set.
    """
    whitelist = set()
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    whitelist.add(line.lower())
    except FileNotFoundError:
        pass
    return whitelist


def is_whitelisted(email, whitelist):
    """
    Returns True if any whitelisted address appears in the sender field.
    Checks substring so "Name <addr@domain.com>" still matches "addr@domain.com".
    """
    sender = email.get("sender", "").lower()
    return any(addr in sender for addr in whitelist)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sweep — Gmail recruitment cleaner")
    parser.add_argument(
        "--archive", action="store_true",
        help="Actually archive recruitment emails (default is dry run — safe to run)",
    )
    parser.add_argument(
        "--unsubscribe", action="store_true",
        help="Also attempt to unsubscribe from each archived email. Requires --archive.",
    )
    parser.add_argument(
        "--max", type=int, default=100,
        help="Max inbox messages to scan (default: 100)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Print per-email classification details and decoded body preview. "
             "Use this to diagnose why a specific email isn't being caught.",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Dry run first, then prompt 'Archive? [y/N]' at the end. "
             "Avoids scanning the same emails twice compared to running dry then --archive.",
    )
    args = parser.parse_args()

    console = Console()

    # Interactive TUI: if run with no flags at all (e.g. a fresh `python sweep.py`),
    # walk the user through it instead of making them learn command-line options.
    # We just gather a scan size and then behave exactly like --confirm: a safe
    # dry run first, then a yes/no prompt before anything is archived.
    if len(sys.argv) == 1:
        console.print("\n[bold]Sweep[/bold] — tidy college-recruitment spam out of your Gmail.")
        console.print(
            "[dim]Archives only (removes the Inbox label). Never deletes. "
            "Nothing changes until you confirm.[/dim]\n"
        )
        try:
            args.max = max(1, IntPrompt.ask("How many recent emails should I scan?", default=100))
        except (KeyboardInterrupt, EOFError):
            console.print("\nCancelled — nothing was changed.")
            return
        args.confirm = True   # dry run, then ask before archiving
        console.print()

    if not args.archive and not args.confirm:
        console.print("\n[bold yellow]DRY RUN[/bold yellow] — nothing will be changed. "
                      "Pass [bold]--confirm[/bold] to review then archive, or [bold]--archive[/bold] to archive immediately.\n")

    if args.unsubscribe and not args.archive and not args.confirm:
        console.print("[yellow]Note: --unsubscribe has no effect without --archive or --confirm.[/yellow]\n")

    # Load whitelist and seen-IDs cache before touching Gmail.
    whitelist = load_whitelist()
    if whitelist:
        console.print(f"[dim]Whitelist loaded: {len(whitelist)} address(es)[/dim]")
    seen_ids = load_seen_ids()
    if seen_ids:
        console.print(f"[dim]Seen-IDs cache: {len(seen_ids)} message(s) will be skipped[/dim]")
    console.print()

    # Authenticate and fetch message IDs.
    service = get_service()
    console.print(f"Fetching up to [bold]{args.max}[/bold] inbox messages...")
    msg_ids = get_inbox_ids(service, args.max)
    console.print(f"Found [bold]{len(msg_ids)}[/bold] messages to scan.\n")

    counts = {
        "archived":     0,
        "kept":         0,
        "skip_review":  0,
        "out_of_scope": 0,
        "whitelisted":  0,
        "already_seen": 0,
    }
    archived_list    = []
    skip_review_list = []
    email_log_entries = []   # one entry per email, written to sweep_log.json
    new_seen_ids = set()     # IDs decided non-recruitment this run; saved at end

    # Process each message with a progress bar.
    for msg_ref in tqdm(msg_ids, desc="Scanning", unit="email"):
        # Skip before fetching — saves one Gmail API call per already-seen message.
        if msg_ref["id"] in seen_ids:
            counts["already_seen"] += 1
            continue

        try:
            email = get_message(service, msg_ref["id"])
        except Exception as e:
            tqdm.write(f"  [skip] could not fetch {msg_ref['id']}: {e}")
            continue

        # Whitelisted senders are never classified or archived.
        if is_whitelisted(email, whitelist):
            counts["whitelisted"] += 1
            new_seen_ids.add(email["id"])
            email_log_entries.append({
                "id":          email["id"],
                "label":       "whitelisted",
                "tier":        "none",
                "confidence":  1.0,
                "reason":      "whitelisted sender",
                "action_taken": "whitelisted",
            })
            continue

        result = classify(email, my_name="Alex")
        label  = result["label"]
        tier   = determine_tier(result["reason"])

        # --debug: show exactly what the classifier saw for every email.
        # Body preview helps diagnose why a phrase isn't being matched.
        if args.debug:
            tqdm.write(
                f"\n  [{label:13}] tier={tier} conf={result['confidence']:.0%}\n"
                f"  from:    {email['sender'][:70]}\n"
                f"  subject: {email['subject'][:70]}\n"
                f"  body[0:200]: {email['body'][:200]!r}\n"
                f"  reason:  {result['reason']}"
            )

        # Determine what action was actually taken (for the log).
        if label == "recruitment" and args.archive:
            action_taken = "archived"
        elif label == "recruitment":
            action_taken = "would_archive"
        else:
            action_taken = label   # "keep", "out_of_scope", "skip_review"

        # Collect the log entry. Only the Gmail message ID is persisted —
        # no sender, subject, or body content ever lands on disk. The ID lets
        # us trace an entry back to the email in Gmail without storing its text.
        email_log_entries.append({
            "id":          email["id"],
            "label":       label,
            "tier":        tier,
            "confidence":  result["confidence"],
            "reason":      result["reason"],
            "action_taken": action_taken,
        })

        if label == "out_of_scope":
            counts["out_of_scope"] += 1
            new_seen_ids.add(email["id"])

        elif label == "keep":
            counts["kept"] += 1
            new_seen_ids.add(email["id"])

        elif label == "recruitment":
            counts["archived"] += 1
            archived_list.append(email)
            if args.archive:
                archive_message(service, email["id"])
                if args.unsubscribe:
                    outcome = try_unsubscribe(service, email)
                    tqdm.write(f"  ↳ unsub: {outcome}")
            # Archived emails are not added to seen_ids — they leave the inbox
            # naturally, and any manually unarchived email should be re-scanned.

        elif label == "skip_review":
            counts["skip_review"] += 1
            new_seen_ids.add(email["id"])
            skip_review_list.append((email, result))

    # Persist newly seen IDs so the next run skips them.
    if new_seen_ids:
        save_seen_ids(new_seen_ids)

    # --- Results table ---
    console.print()
    table = Table(title="Sweep Results", show_header=True, header_style="bold cyan")
    table.add_column("Category")
    table.add_column("Count", justify="right")

    action = "Archived" if args.archive else "Would archive"
    table.add_row(f"{action} (recruitment)", str(counts["archived"]),    style="red")
    table.add_row("Kept (personal)",          str(counts["kept"]),        style="green")
    table.add_row("Flagged for review",        str(counts["skip_review"]), style="yellow")
    table.add_row("Skipped (non-college)",     str(counts["out_of_scope"]))
    console.print(table)

    # List archived / would-archive emails.
    if archived_list:
        console.print(f"\n[bold]{'Archived' if args.archive else 'Would archive'} ({len(archived_list)}):[/bold]")
        for e in archived_list:
            console.print(f"  [red]•[/red] {e['sender'][:45]:<45}  {e['subject'][:55]}")

    # Review pile.
    if skip_review_list:
        console.print(f"\n[bold yellow]Flagged for manual review ({len(skip_review_list)}):[/bold yellow]")
        console.print("  LLM leaned recruitment but wasn't confident enough to archive.")
        for e, r in skip_review_list:
            console.print(f"  [yellow]•[/yellow] {e['subject'][:60]}  [{r['reason']}]")

    # --confirm: prompt to archive after reviewing the list — no re-scan needed.
    confirmed = False
    if args.confirm and archived_list and not args.archive:
        console.print(f"\nArchive these [bold]{len(archived_list)}[/bold] email(s)? [[bold green]y[/bold green]/N] ", end="")
        if input().strip().lower() == "y":
            confirmed = True
            for e in archived_list:
                archive_message(service, e["id"])
                if args.unsubscribe:
                    outcome = try_unsubscribe(service, e)
                    console.print(f"  ↳ unsub: {outcome}")
            # Upgrade log entries so they reflect what actually happened.
            for entry in email_log_entries:
                if entry["action_taken"] == "would_archive":
                    entry["action_taken"] = "archived"
            console.print(f"[green]✓ Archived {len(archived_list)} email(s).[/green]")

    # --- Write JSON log (append this run to existing file) ---
    # Written after --confirm so the mode reflects what actually happened.
    run_record = {
        "timestamp":     datetime.now().isoformat(),
        "mode":          "archive" if (args.archive or confirmed) else "dry_run",
        "total_scanned": len(msg_ids),
        "emails":        email_log_entries,
    }
    try:
        with open(LOG_FILE, "r") as f:
            all_runs = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        all_runs = []
    all_runs.append(run_record)
    with open(LOG_FILE, "w") as f:
        json.dump(all_runs, f, indent=2)

    # --- Stats summary ---
    total   = len(email_log_entries)
    decided = [e for e in email_log_entries if e["label"] not in ("out_of_scope", "whitelisted")]
    rule_n     = sum(1 for e in decided if e["tier"] == "rule")
    default_n  = sum(1 for e in decided if e["tier"] == "default")
    rule_pct   = rule_n / len(decided) * 100 if decided else 0
    default_pct = default_n / len(decided) * 100 if decided else 0

    console.print()
    stats = Table(title="Decision Stats", show_header=True, header_style="bold")
    stats.add_column("Metric")
    stats.add_column("Value", justify="right")
    n_decided = len(decided)
    stats.add_row("Total scanned",                    str(total))
    stats.add_row("Already seen (skipped)",            str(counts["already_seen"]), style="dim")
    stats.add_row("Whitelisted (never classified)",    str(counts["whitelisted"]),  style="cyan")
    stats.add_row("Out of scope (non-college)",        str(counts["out_of_scope"]))
    stats.add_row("In college scope (decided)",        str(n_decided))
    stats.add_row("  Archived / would archive",        str(counts["archived"]),    style="red")
    stats.add_row("  Kept",                            str(counts["kept"]),        style="green")
    stats.add_row("  Flagged for review",              str(counts["skip_review"]), style="yellow")
    stats.add_row("Rule decisions (of in-scope)",      f"{rule_n} of {n_decided}  ({rule_pct:.0f}%)")
    stats.add_row("Kept by default (ambiguous)",        f"{default_n} of {n_decided}  ({default_pct:.0f}%)")
    console.print(stats)

    log_path = os.path.abspath(LOG_FILE)
    console.print(f"\n[dim]Log appended → {log_path}[/dim]")

    if not args.archive and not confirmed:
        console.print("[dim]Run with [bold]--archive[/bold] or [bold]--confirm[/bold] to apply changes.[/dim]")


if __name__ == "__main__":
    main()
