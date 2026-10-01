"""
send_digest.py — Weekly HTML email digest of new UK tenders.

Required GitHub Actions secrets:
  RESEND_API_KEY   – from resend.com (free: 100 emails/day, 3k/month)
  DIGEST_FROM      – verified sender address, e.g. "tenders@yourdomain.com"
  DIGEST_TO        – recipient(s), comma-separated, e.g. "you@example.com,team@example.com"

Optional:
  DASHBOARD_URL    – your GitHub Pages URL shown in email footer
  LOOKBACK_DAYS    – how many days back to look (default: 7)
"""

import json, os, sys, datetime as dt
import requests

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get("RESEND_API_KEY", "")
FROM_ADDR    = os.environ.get("DIGEST_FROM", "")
TO_ADDRS     = [e.strip() for e in os.environ.get("DIGEST_TO", "").split(",") if e.strip()]
DASHBOARD    = os.environ.get("DASHBOARD_URL", "https://your-github-pages-url")
LOOKBACK     = int(os.environ.get("LOOKBACK_DAYS", "7"))
TENDERS_FILE = "tenders.json"

SOURCE_COLORS = {
    "CF":  "#22d3ee", "FTS": "#6c8fff", "PCS": "#34d399",
    "S2W": "#fb7185", "NI":  "#f59e0b", "TED": "#a78bfa",
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
    if v is None: return "Value unknown"
    return "£" + f"{int(v):,}"

def days_left(deadline: str | None) -> int | None:
    if not deadline: return None
    try:
        d = dt.datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return max(0, (d - dt.datetime.now(dt.timezone.utc)).days)
    except Exception:
        return None

# ── Load tenders ──────────────────────────────────────────────────────────────
if not os.path.exists(TENDERS_FILE):
    print(f"[digest] {TENDERS_FILE} not found — aborting")
    sys.exit(0)

with open(TENDERS_FILE, encoding="utf-8") as f:
    data = json.load(f)

all_tenders  = data.get("tenders", [])
generated_at = data.get("generated", "")
cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK)

new_tenders = [
    t for t in all_tenders
    if t.get("first_seen") and
       dt.datetime.fromisoformat(t["first_seen"].replace("Z", "+00:00")) >= cutoff
]

print(f"[digest] {len(all_tenders)} total, {len(new_tenders)} new in last {LOOKBACK}d")

if not new_tenders:
    print("[digest] Nothing new — skipping email")
    sys.exit(0)

if not API_KEY or not FROM_ADDR or not TO_ADDRS:
    print("[digest] Missing RESEND_API_KEY / DIGEST_FROM / DIGEST_TO — skipping")
    sys.exit(0)

# ── Group by category ─────────────────────────────────────────────────────────
from collections import defaultdict
by_cat: dict[str, list] = defaultdict(list)
for t in sorted(new_tenders, key=lambda x: x.get("deadline") or "9999"):
    for cat in (t.get("categories") or ["Uncategorised"]):
        by_cat[cat].append(t)

# ── Build HTML ────────────────────────────────────────────────────────────────
HEADER_BG   = "#0d1117"
CARD_BG     = "#161b22"
BORDER      = "#30363d"
TEXT_MAIN   = "#e6edf3"
TEXT_MUTED  = "#8b949e"
ACCENT      = "#6c8fff"

def tender_row(t: dict) -> str:
    code   = src_code(t.get("source", ""))
    color  = SOURCE_COLORS.get(code, "#6c8fff")
    dl     = days_left(t.get("deadline"))
    dl_str = f"{dl}d left" if dl is not None else "No deadline"
    dl_col = "#f87171" if (dl is not None and dl <= 3) else \
             "#f59e0b" if (dl is not None and dl <= 7) else TEXT_MUTED
    return f"""
    <tr>
      <td style="padding:14px 16px;border-bottom:1px solid {BORDER};vertical-align:top">
        <a href="{t.get('url','#')}" style="color:{ACCENT};text-decoration:none;font-weight:600;font-size:14px;line-height:1.4;display:block;margin-bottom:6px">
          {t.get('title','Untitled')[:120]}{'…' if len(t.get('title',''))>120 else ''}
        </a>
        <span style="display:inline-block;background:{color}22;color:{color};border:1px solid {color}55;border-radius:10px;font-size:10px;font-weight:700;padding:1px 7px;margin-right:6px;letter-spacing:.04em">{code}</span>
        <span style="color:{TEXT_MUTED};font-size:12px">{t.get('buyer','Unknown buyer')}</span>
        <span style="color:{TEXT_MUTED};font-size:12px;margin:0 6px">·</span>
        <span style="color:#34d399;font-size:12px;font-weight:600">{gbp(t.get('value'))}</span>
        <span style="color:{TEXT_MUTED};font-size:12px;margin:0 6px">·</span>
        <span style="color:{dl_col};font-size:12px;font-weight:600">{dl_str}</span>
      </td>
    </tr>"""

cat_sections = ""
for cat, tenders in sorted(by_cat.items()):
    rows = "".join(tender_row(t) for t in tenders[:20])  # cap at 20 per cat
    cat_sections += f"""
    <tr><td style="padding:20px 16px 6px">
      <h2 style="margin:0;font-size:14px;font-weight:700;color:{TEXT_MAIN};letter-spacing:.05em;text-transform:uppercase;border-left:3px solid {ACCENT};padding-left:10px">
        {cat} <span style="color:{TEXT_MUTED};font-weight:400;font-size:12px">({len(tenders)} tender{'s' if len(tenders)!=1 else ''})</span>
      </h2>
    </td></tr>
    {rows}"""

week_str = cutoff.strftime("%-d %b") + " – " + dt.datetime.now(dt.timezone.utc).strftime("%-d %b %Y")

html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>UK Tenders Digest — {week_str}</title>
</head>
<body style="margin:0;padding:0;background:#0a0c10;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#0a0c10;padding:32px 0">
  <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" border="0" style="max-width:640px;width:100%">

      <!-- Header -->
      <tr><td style="background:{HEADER_BG};border-radius:12px 12px 0 0;padding:28px 32px;border:1px solid {BORDER};border-bottom:none">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td>
              <div style="font-size:22px;font-weight:700;color:{TEXT_MAIN}">📋 UK Tenders Digest</div>
              <div style="font-size:13px;color:{TEXT_MUTED};margin-top:4px">{week_str} &nbsp;·&nbsp; {len(new_tenders)} new tender{'s' if len(new_tenders)!=1 else ''}</div>
            </td>
            <td align="right">
              <a href="{DASHBOARD}" style="display:inline-block;background:{ACCENT};color:#fff;text-decoration:none;font-size:12px;font-weight:600;padding:8px 16px;border-radius:8px">View Dashboard →</a>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Stats bar -->
      <tr><td style="background:#111827;border-left:1px solid {BORDER};border-right:1px solid {BORDER};padding:16px 32px">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            {"".join(f'''<td align="center" style="border-right:1px solid {BORDER};padding:0 16px">
              <div style="font-size:24px;font-weight:700;color:{TEXT_MAIN}">{count}</div>
              <div style="font-size:11px;color:{TEXT_MUTED};text-transform:uppercase;letter-spacing:.06em;margin-top:2px">{cat[:18]}</div>
            </td>''' for cat, tenders in list(sorted(by_cat.items()))[:4] for count in [len(tenders)])}
          </tr>
        </table>
      </td></tr>

      <!-- Tender rows -->
      <tr><td style="background:{CARD_BG};border:1px solid {BORDER};border-top:none;border-radius:0 0 12px 12px;padding:0 16px 16px">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {cat_sections}
        </table>
      </td></tr>

      <!-- Footer -->
      <tr><td style="padding:24px 0;text-align:center">
        <div style="font-size:11px;color:#4b5563">
          Generated {dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y %H:%M UTC')} &nbsp;·&nbsp;
          <a href="{DASHBOARD}" style="color:{TEXT_MUTED}">Dashboard</a> &nbsp;·&nbsp;
          Sources: Contracts Finder, Find a Tender, Public Contracts Scotland, Sell2Wales, eTendersNI
        </div>
      </td></tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

# ── Send via Resend ───────────────────────────────────────────────────────────
subject = f"UK Tenders Digest — {len(new_tenders)} new tenders ({week_str})"
payload = {
    "from":    FROM_ADDR,
    "to":      TO_ADDRS,
    "subject": subject,
    "html":    html,
}
resp = requests.post(
    "https://api.resend.com/emails",
    json=payload,
    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    timeout=30,
)
if resp.ok:
    print(f"[digest] ✓ Sent to {TO_ADDRS} — {resp.json().get('id')}")
else:
    print(f"[digest] ✗ Resend error {resp.status_code}: {resp.text}")
    sys.exit(1)
