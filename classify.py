"""
classify.py — tiered email classifier

Runs two rule tiers in order, stopping as soon as a tier decides:
  Tier 1: is_definite_keep()       — strong personal signals → keep
  Tier 2: is_definite_recruitment() — strong spam signals    → archive

Classification is entirely rule-based. Anything the rules can't decide
defaults to "keep" — we never archive on a guess.

The top-level classify() function runs both tiers and returns a unified dict.
"""


# ---------------------------------------------------------------------------
# Phrase lists — edit these to tune the classifier without touching logic 
# ---------------------------------------------------------------------------

# ANY of these in subject+body → almost certainly a personal email, keep it.
# Phrases are intentionally specific — they only appear when an application
# already exists, not in CTAs encouraging you to start one.
KEEP_PHRASES = [
    "we received your application",   # application already submitted
    "your application has",           # "has been reviewed / received / accepted"
    "congratulations",                # acceptances, awards
    "admission decision",             # a decision is being communicated
    "enrollment deposit",             # post-acceptance action
    # Removed "your application" (too broad — fires on "start your application")
    # Removed "next steps" (too broad — spam says "next steps to apply")
    # Removed "your enrollment" — fires on "explore your enrollment options" in spam
    # Removed "your admission" — fires on "start your admission journey" in spam
    # "admission decision" and "enrollment deposit" cover the real post-acceptance cases
]

# ANY of these in subject+body → almost certainly mass college-recruitment spam.
# Phrases must be specific enough that a non-college .edu site (e.g. academia.edu)
# would never produce them. Generic phrases like "apply now" or "request information"
# were removed because they appear in commercial upsell emails too — those edge
# cases fall through to the default "keep" instead.
RECRUITMENT_PHRASES = [
    "students like you",       # explicitly describes a mailing list blast
    "start your application",  # college-application CTA, not a generic "apply"
    "visit our campus",        # only universities have campuses to visit
    "campus tour",             # tour invitation — college-only phrase
    "tour our campus",         # variant phrasing of the same thing
    "schedule a tour",         # another variant
    "visit day",               # formal campus-visit event
    "open house",              # college open-house event
    "explore your future",     # college-recruitment marketing language
    "discover your potential", # college-recruitment marketing language
    "i encourage you to apply",# admissions officer outreach phrasing
]

# Colleges outsource mass emails to these marketing platforms.
# If the sender's email address contains one of these domains, it's a blast.
SPAM_SENDER_DOMAINS = [
    "emsihe.com",             # Liaison/TargetX enrollment marketing
    "marketo.com",            # Adobe Marketo
    "exacttarget.com",        # Salesforce Marketing Cloud
    "salesforceemail.com",
    "pardot.com",             # another Salesforce product
]

# Words for non-.edu senders (marketing platforms, abbreviated school names).
# Broad because these senders have no other signal — "apply" in a Mailchimp
# email from "BGSU Visit Team" is almost certainly college-related.
COLLEGE_KEYWORDS = [
    "university", "college", "admissions", "admission", "campus",
    "undergraduate", "enrollment", "applicant", "tuition",
    # Removed: "apply", "application", "academic", "scholarship", "major",
    # "transfer", "degree" — all too broad. They appear in Pacsun promo copy,
    # Grammarly marketing, LinkedIn alerts, UNIQLO ("MAJOR deals"), Taco Bell
    # rewards ("transfer points"), etc. Every one of those was getting kept
    # anyway, so the words added no value. The remaining keywords are
    # specific enough that no commercial non-college email uses them.
    "visit day", "campus tour", "tour campus", "information session",
    "class of", "freshman", "first-year", "student life",
]

# Stricter set used only for .edu senders.
# "apply" and "application" are deliberately excluded — commercial .edu platforms
# (academia.edu, researchgate.net) use them for subscriptions and paper uploads.
# Real university emails about admissions almost always contain at least one of these.
EDU_ADMISSIONS_KEYWORDS = [
    "campus", "admissions", "admission", "enrollment", "undergraduate",
    "class of", "open house", "visit day", "campus tour", "tour campus",
]


# ---------------------------------------------------------------------------
# Scope gate — runs before any tier
# ---------------------------------------------------------------------------

def is_college_related(email: dict) -> bool:
    """
    Returns True if the email is plausibly from or about a college.
    If False, classify() returns "out_of_scope" — never archived.

    Check order matters:
      1. Known college marketing platform sender → always in scope.
      2. College-identifying word in the sender address → always in scope.
      3. .edu sender → only in scope if the content has admissions-specific words.
         Why not .edu alone? Commercial platforms (academia.edu, researchgate.net)
         have .edu domains but their emails are about papers and subscriptions.
         Real university recruitment emails almost always say "campus", "enrollment",
         "admissions", etc. Academia.edu's "Apply now for Premium" does not.
      4. Non-.edu sender → in scope if the broader COLLEGE_KEYWORDS list matches.
         Non-.edu college marketing emails often use abbreviated school names and
         broader language ("apply", "scholarship") that academia.edu avoids.
    """
    sender = email.get("sender", "").lower()
    text   = (email.get("subject", "") + " " + email.get("body", "")).lower()

    # Known college marketing platforms only send college mail.
    if any(domain in sender for domain in SPAM_SENDER_DOMAINS):
        return True

    # College word in sender address (e.g. "admissions@gmail.com", rare but real).
    if any(word in sender for word in ["admissions", "enrollment", "university", "college"]):
        return True

    # .edu sender: require admissions-specific content, not just any keyword.
    # "apply" and "application" excluded — academia.edu uses them commercially.
    if ".edu" in sender:
        return any(kw in text for kw in EDU_ADMISSIONS_KEYWORDS)

    # Non-.edu sender (Mailchimp, Hobsons, etc.): use the broader keyword list.
    return any(keyword in text for keyword in COLLEGE_KEYWORDS)


# ---------------------------------------------------------------------------
# Tier 1 — definite keep
# ---------------------------------------------------------------------------

def is_definite_keep(email: dict) -> bool:
    """
    Returns True if the email contains strong personal signals.
    This tier runs FIRST — if it fires, we never archive, full stop.

    Why check keep before recruitment?
    Safety. It's worse to archive a real acceptance than to keep a spam email.
    An email can theoretically have both signals (rare), and keep wins.

    email dict expected keys: "subject" (str), "body" (str)
    """
    # Combine subject + body, lowercased, so one search covers both.
    text = (email.get("subject", "") + " " + email.get("body", "")).lower()
    return any(phrase in text for phrase in KEEP_PHRASES)


# ---------------------------------------------------------------------------
# Tier 2 — definite recruitment
# ---------------------------------------------------------------------------

def is_definite_recruitment(email: dict) -> bool:
    """
    Returns True if the email has clear mass-recruitment signals.
    Only called after is_definite_keep() returned False.

    CTA signals fall into two tiers:
      strong_cta  — phrases specific enough to college recruitment to count alone
                    (e.g. "visit our campus", "students like you")
      apply_now   — "apply now" is extremely common in real college spam but also
                    appears in commercial upsells (academia.edu premium, job boards).
                    It only counts when paired with a college-context signal:
                    "admissions" in the sender address, or "campus" in the body.

    Why "admissions" and "campus" as pairing signals?
      Academia.edu's sender is message@academia.edu — no "admissions".
      Academia.edu upsell emails never mention a campus.
      Real college recruitment almost always has one or both.

    email dict expected keys: "subject" (str), "body" (str), "sender" (str)
    """
    text   = (email.get("subject", "") + " " + email.get("body", "")).lower()
    sender = email.get("sender", "").lower()

    is_spam_sender  = any(domain in sender for domain in SPAM_SENDER_DOMAINS)
    has_unsubscribe = "unsubscribe" in text
    has_strong_cta  = any(phrase in text for phrase in RECRUITMENT_PHRASES)

    # "apply now" requires a college-context signal to avoid false positives
    # from commercial .edu sites (academia.edu, job boards, etc.)
    has_apply_now     = "apply now" in text
    college_context   = "admissions" in sender or "campus" in text
    apply_now_counts  = has_apply_now and college_context

    has_any_cta = has_strong_cta or apply_now_counts

    # Admissions-role sender + unsubscribe = bulk outreach, full stop.
    # Words like "admissions", "admission", "enroll" in a sender address
    # identify the sender as a college recruitment office. Personal emails and
    # post-application emails from those same offices are caught by Tier 1 first,
    # so this rule never fires on a real acceptance or decision email.
    is_admissions_sender = any(
        word in sender
        for word in ["admissions", "admission", "undergraduateadmissions", "enroll"]
    )
    if is_admissions_sender and has_unsubscribe:
        return True

    # Marketing platform sender alone is definitive — these only send college blasts.
    if is_spam_sender:
        return True

    # Unsubscribe + any CTA → bulk college mail, not a personal email.
    if has_unsubscribe and has_any_cta:
        return True

    # CTA alone is strong enough within the college scope.
    return has_any_cta


# ---------------------------------------------------------------------------
# Top-level classify() — the function sweep.py will call
# ---------------------------------------------------------------------------

def classify(email: dict, my_name: str = "Alex") -> dict:
    """
    Classifies one email. Returns:
      {
        "label":      "keep" | "recruitment" | "out_of_scope",
        "reason":     human-readable explanation,
        "confidence": float 0.0–1.0
      }

    Classification is entirely rule-based. An email is only archived when a
    rule positively identifies it as recruitment; everything the rules can't
    decide defaults to "keep".
    "out_of_scope" means the email has no college connection — never archived.
    """
    sender = email.get("sender", "").lower()

    if not is_college_related(email):
        if ".edu" in sender:
            # .edu domain but no admissions keywords in content.
            # Could be a real school with a short/vague subject (UChicago, Earlham, BGSU),
            # or a commercial .edu platform (academia.edu).
            # Rules can't tell them apart here, so we keep it. Never archive on a guess.
            return {
                "label": "keep",
                "reason": "ambiguous (.edu, no admissions keywords) — keeping by default",
                "confidence": 0.0,
            }
        # Non-.edu with no college signals → definitely skip, never archive.
        return {
            "label": "out_of_scope",
            "reason": "no college signals — skipping entirely",
            "confidence": 1.0,
        }

    if is_definite_keep(email):
        return {
            "label": "keep",
            "reason": "rule: keep phrase detected",
            "confidence": 1.0,
        }

    if is_definite_recruitment(email):
        return {
            "label": "recruitment",
            "reason": "rule: spam signal detected",
            "confidence": 1.0,
        }

    # In college scope but no rule fired — genuinely ambiguous.
    # With no LLM in the loop, we default to keep rather than risk archiving
    # a real email. Safe failure by design.
    return {
        "label": "keep",
        "reason": "ambiguous — no rule fired, keeping by default",
        "confidence": 0.0,
    }


# ---------------------------------------------------------------------------
# Quick self-test — runs only when you do: python classify.py
# No Gmail connection needed.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    samples = [
        {
            "name": "Real acceptance",
            "sender": "admissions@university.edu",
            "subject": "Congratulations on your admission decision",
            "body": "Dear Alex, Congratulations! We are pleased to inform you that your application to the Class of 2029 has been accepted. Please review your financial aid award and next steps below.",
        },
        {
            "name": "Obvious spam (unsubscribe)",
            "sender": "info@emsihe.com",
            "subject": "Students like you are discovering State University",
            "body": "Hi Alex, Students like you are finding their future at State U. Apply now and explore your potential. To unsubscribe from these emails click here.",
        },
        {
            "name": "Obvious spam (CTA)",
            "sender": "outreach@somecollegemail.edu",
            "subject": "Start your application today!",
            "body": "Hi Alex, We encourage you to start your application and learn more about our programs. Visit our campus this fall.",
        },
        {
            "name": "Ambiguous — kept by default",
            "sender": "admissions@college.edu",
            "subject": "An update from the admissions office",
            "body": "Hi Alex, We wanted to reach out and share some updates about our upcoming application deadline. We think you'd be a great fit here.",
        },
        {
            # "apply now" present + unsubscribe present, but no "admissions" in sender
            # and no "campus" in body → apply_now_counts=False → kept by default, not archived.
            "name": "Academia.edu upsell (KEEP — never recruitment)",
            "sender": "message@academia.edu",
            "subject": "Try Academia Premium",
            "body": "Hi Alex, Apply now to get unlimited access to papers. To unsubscribe click here.",
        },
        {
            # Same "apply now" phrase, but sender has "admissions" → apply_now_counts=True → recruitment.
            "name": "College spam with apply now + admissions sender (recruitment)",
            "sender": "admissions@bucknell.edu",
            "subject": "Apply Now for Fall 2026",
            "body": "Hi Alex, We encourage you to apply now. To unsubscribe click here.",
        },
        {
            "name": "Non-college newsletter (should be OUT OF SCOPE)",
            "sender": "newsletter@cooking-weekly.com",
            "subject": "This week's best recipes",
            "body": "Hi Alex, Check out this week's top recipes. To unsubscribe from this newsletter click here.",
        },
        {
            "name": "College email with unsubscribe but no CTA (should be KEEP)",
            "sender": "news@mit.edu",
            "subject": "Campus update — spring semester",
            "body": "Hi Alex, Here is your monthly campus news digest. No action needed. To unsubscribe from campus updates click here.",
        },
    ]

    # Scope-gate check — verify real schools aren't blocked before classification.
    print("--- Scope gate check ---\n")
    scope_cases = [
        # .edu sender — the baseline that must always pass
        {"name": "Direct .edu sender",        "sender": "info@earlham.edu",           "subject": "Hello", "body": ""},
        # Marketing platform + abbreviated name — the cases that were failing
        {"name": "BGSU via mailchimp",         "sender": "BGSU Visit Team <noreply@mailchimp.com>", "subject": "Visit BGSU this fall", "body": ""},
        {"name": "UChicago abbreviated",       "sender": "UChicago <outreach@email.uchicago.edu>",  "subject": "Discover UChicago",    "body": ""},
        {"name": "Skidmore no .edu in From",   "sender": "Skidmore <info@mail.skidmore.edu>",       "subject": "Apply to Skidmore",    "body": ""},
        {"name": "BGSU open house subject",    "sender": "BGSU Visit Team <noreply@sender.com>",    "subject": "Join us for Open House", "body": ""},
        # Non-college should still be blocked
        {"name": "Cooking newsletter (block)", "sender": "news@cooking-weekly.com",   "subject": "This week's recipes", "body": ""},
    ]
    for c in scope_cases:
        result = is_college_related(c)
        status = "IN SCOPE  ✓" if result else "BLOCKED   ✗"
        print(f"  [{status}] {c['name']}")

    print("\n--- Full classification (rule-based) ---\n")
    for s in samples:
        result = classify(s, my_name="Alex")
        print(f"[{s['name']}]")
        print(f"  → label:      {result['label']}")
        print(f"  → reason:     {result['reason']}")
        print(f"  → confidence: {result['confidence']:.0%}")
        print()
