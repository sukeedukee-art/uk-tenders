"""
send_alerts.py — Post today's new tenders to Slack and/or Microsoft Teams.

Required GitHub Actions secrets (set whichever you use):
  SLACK_WEBHOOK_URL   – Slack Incoming Webhook URL
  TEAMS_WEBHOOK_URL   – Teams Incoming Webhook URL

Optional:
  DASHBOARD_URL       – shown in message footer
  ALERT_LOOKBACK_H    – hours back to look for "new" (default: 25, covers slight timing drift)
  ALERT_MAX_CARDS     – max tender cards per message (default: 10, Slack hard-limits at 50 blocks)
"""

import json, os, sys, datetime as dt
import requests

# ── Config ────────────────────────────────────────────────────────────────────
SLACK_WEBHOOK  = os.environ.get("SLACK_WEBHOOK_URL", "")
TEAMS_WEBHOOK  = os.environ.get("TEAMS_WEBHOOK_URL", "")
DASHBOARD      = os.environ.get("DASHBOARD_URL", "https://your-github-pages-url")
LOOKBACK_H     = int(os.environ.get("ALERT_LOOKBACK_H", "25"))
MAX_CARDS      = int(os.environ.get("ALERT_MAX_CARDS", "10"))
TENDERS_FILE   = "tenders.json"

SOURCE_EMOJI = {
    "CF": "🔵", "FTS": "🟣", "PCS": "🟢",
    "S2W": "🩷", "NI": "🟡", "TED": "🔮",
}

def src_code(source: str) -> str:
    if "contracts finder" in source.lower(): return "CF"
    if "find a tender"    in source.lower(): return "FTS"
    if "scotland"         in source.lower(): return "PCS"
    if "wales"            in source.lower(): return "S2W"
    if any(x in source.lower() for x in ("ni", "northern ireland")): return "NI"
    if "ted"              in source.lower(): return "TED"
    return "CF"

def gbp(v) -> str:
    return f"£{int(v):,}" if v is not None else "Value n/a"

def days_left(deadline: str | None) -> str:
    if not deadline: return "No deadline"
    try:
        d = dt.datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        n = (d - dt.datetime.now(dt.timezone.utc)).days
        return f"{max(0,n)}d left"
    except Exception:
        return "?"

# ── Load tenders ──────────────────────────────────────────────────────────────
if not os.path.exists(TENDERS_FILE):
    print(f"[alerts] {TENDERS_FILE} not found — aborting")
    sys.exit(0)

with open(TENDERS_FILE, encoding="utf-8") as f:
    data = json.load(f)

all_tenders = data.get("tenders", [])
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_H)

new_tenders = [
    t for t in all_tenders
    if t.get("first_seen") and
       dt.datetime.fromisoformat(t["first_seen"].replace("Z", "+00:00")) >= cutoff
]

print(f"[alerts] {len(new_tenders)} new tenders in last {LOOKBACK_H}h")

if not new_tenders:
    print("[alerts] Nothing new — no alerts sent")
    sys.exit(0)

if not SLACK_WEBHOOK and not TEAMS_WEBHOOK:
    print("[alerts] No webhook configured — set SLACK_WEBHOOK_URL or TEAMS_WEBHOOK_URL secret")
    sys.exit(0)

featured  = new_tenders[:MAX_CARDS]
overflow  = max(0, len(new_tenders) - MAX_CARDS)

# ── Slack payload ─────────────────────────────────────────────────────────────
def build_slack_payload() -> dict:
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📋 UK Tenders — {len(new_tenders)} new today ({date_str})", "emoji": True}
        },
        {"type": "divider"},
    ]

    for t in featured:
        code    = src_code(t.get("source", ""))
        emoji   = SOURCE_EMOJI.get(code, "📄")
        cats    = ", ".join(t.get("categories") or ["General"])
        title   = (t.get("title") or "Untitled")[:120]
        buyer   = t.get("buyer") or "Unknown buyer"
        dl      = days_left(t.get("deadline"))
        val     = gbp(t.get("value"))
        url     = t.get("url", "#")

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{emoji} *{code}* · <{url}|{title}>\n"
                    f"_{buyer}_ · *{val}* · ⏰ {dl}\n"
                    f"🏷 {cats}"
                )
            }
        })
        blocks.append({"type": "divider"})

    if overflow:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_…and {overflow} more. <{DASHBOARD}|View all on dashboard →>_"}
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{DASHBOARD}|View dashboard →>"}
        })

    return {"blocks": blocks}


# ── Teams payload (Adaptive Card) ─────────────────────────────────────────────
def build_teams_payload() -> dict:
    date_str = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y")
    facts = []
    for t in featured:
        code  = src_code(t.get("source", ""))
        emoji = SOURCE_EMOJI.get(code, "📄")
        title = (t.get("title") or "Untitled")[:100]
        buyer = t.get("buyer") or "?"
        dl    = days_left(t.get("deadline"))
        val   = gbp(t.get("value"))
        facts.append({
            "title": f"{emoji} [{code}] {title}",
            "value": f"{buyer} · {val} · ⏰ {dl}"
        })

    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder",
         "text": f"📋 UK Tenders — {len(new_tenders)} new today ({date_str})"},
        {"type": "FactSet", "facts": facts},
    ]
    if overflow:
        body.append({"type": "TextBlock", "text": f"…and {overflow} more tender{'s' if overflow!=1 else ''}."})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": body,
                "actions": [{"type": "Action.OpenUrl", "title": "View Dashboard", "url": DASHBOARD}]
            }
        }]
    }


# ── Send ──────────────────────────────────────────────────────────────────────
def post(url: str, payload: dict, label: str):
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.ok:
            print(f"[alerts] ✓ {label} alert sent")
        else:
            print(f"[alerts] ✗ {label} error {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[alerts] ✗ {label} exception: {e}")

if SLACK_WEBHOOK:
    post(SLACK_WEBHOOK, build_slack_payload(), "Slack")

if TEAMS_WEBHOOK:
    post(TEAMS_WEBHOOK, build_teams_payload(), "Teams")
