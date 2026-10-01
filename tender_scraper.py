"""UK live-tender collector.
Sources  : Contracts Finder API              (CF)
           Find a Tender OCDS API            (FTS)
           Public Contracts Scotland Atom    (PCS)
           Sell2Wales Atom                   (S2W)
           eTendersNI Atom                   (NI)
           TED / EU OJEU REST API            (TED) – optional, set TED_API_KEY env var.
Output   : tenders.json  (read by index.html).  Run daily.
"""
from concurrent.futures import ThreadPoolExecutor
import json, re, datetime as dt, requests, os
import xml.etree.ElementTree as ET

UA       = {"User-Agent": "TenderMonitor/1.0 (contact@example.com)"}
OUT      = "tenders.json"
NOW      = dt.datetime.now(dt.timezone.utc)
FTS_BASE = "https://www.find-tender.service.gov.uk"
ATOM_NS  = {"a": "http://www.w3.org/2005/Atom"}

CATEGORIES = {
    "Supported Living": {
        "kw": [
            "supported living", "supported housing", "learning disabilit",
            "mental health accommodation", "care and support", "extra care",
            "residential care", "domiciliary care", "personal care",
            "adults with disabilities", "complex needs", "learning disability",
        ],
        "cpv": ["85311", "85312", "85310", "85000"],
    },
    "Supported Accommodation": {
        "kw": [
            "supported accommodation", "semi-independent", "16+ accommodation",
            "homeless accommodation", "housing related support", "temporary accommodation",
            "rough sleeping", "move-on accommodation", "transitional housing",
            "floating support", "dispersed accommodation",
        ],
        "cpv": ["55", "70"],
    },
    "Construction": {
        "kw": [
            "construction", "refurbishment", "building works", "groundworks",
            "new build", "civil engineering", "modular", "retrofit",
            "renovation", "reinstatement", "demolition",
            "structural works", "cladding", "roofing",
        ],
        "cpv": ["45"],
    },
    "Teleradiology": {
        "kw": [
            "teleradiology", "radiology reporting", "diagnostic imaging",
            "remote reporting", "radiology", "MRI reporting", "CT reporting",
            "x-ray reporting", "radiologist", "imaging reporting",
            "PACS", "picture archiving",
        ],
        "cpv": ["85121", "33111", "85120"],
    },
}

KEYWORD_REQUIRED = {"Supported Accommodation"}

PROCONTRACT_PORTALS = [
    ("Public Contracts Scotland", "https://www.publiccontractsscotland.gov.uk", "PCS"),
    ("Sell2Wales",                "https://www.sell2wales.gov.wales",            "S2W"),
    ("eTendersNI",                "https://etendersni.gov.uk",                   "NI"),
]


# ── Matching & parsing helpers ────────────────────────────────────────────────

def match(text, cpvs):
    text = text.lower()
    hits = []
    for cat, c in CATEGORIES.items():
        kw = any(k.lower() in text for k in c["kw"])
        cp = any(code.startswith(p) for code in cpvs for p in c["cpv"])
        if (kw or (cp and cat not in KEYWORD_REQUIRED)) and cat not in hits:
            hits.append(cat)
    return hits


def parse_dt(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def live(deadline):
    return deadline is None or deadline > NOW


# ── ProContract helpers ───────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _extract_deadline(text: str):
    patterns = [
        r"[Dd]eadline\s*(?:[Dd]ate)?[^:]*:\s*(\d{1,2}[/\- ]\d{2}[/\- ]\d{2,4}(?:\s+\d{1,2}:\d{2})?)",
        r"[Cc]losing\s*(?:[Dd]ate)?[^:]*:\s*(\d{1,2}[/\- ]\d{2}[/\- ]\d{2,4})",
        r"[Rr]eturn\s*[Bb]y[^:]*:\s*(\d{1,2}[/\- ]\d{2}[/\- ]\d{2,4})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            d = parse_dt(m.group(1).strip())
            if d:
                return d
    return None


def _extract_value(text: str):
    m = (re.search(r"[Ee]stimated\s*[Vv]alue[^:]*:\s*[£€]?([\d,]+(?:\.\d+)?)", text) or
         re.search(r"[Cc]ontract\s*[Vv]alue[^:]*:\s*[£€]?([\d,]+(?:\.\d+)?)", text) or
         re.search(r"\b[Vv]alue[^:]*:\s*[£€]([\d,]+(?:\.\d+)?)", text))
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            pass
    return None


def _extract_buyer(text: str):
    m = re.search(
        r"(?:Contracting Authority|Organisation Name?|Authority|Buyer)[^:]*:\s*([^\n<|]{3,100})",
        text, re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


# ── Contracts Finder ──────────────────────────────────────────────────────────

def contracts_finder():
    url = "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json"
    kws = sorted({k for c in CATEGORIES.values() for k in c["kw"]})
    found = {}

    def search(kw):
        results = []
        for page in range(1, 4):
            body = {"searchCriteria": {"keyword": kw, "statuses": ["Open"]},
                    "size": 100, "page": page}
            try:
                r = requests.post(url, json=body, headers=UA, timeout=60)
                r.raise_for_status()
                items = r.json().get("noticeList", [])
            except Exception as e:
                print(f"[CF] '{kw}' p{page} failed: {e}", flush=True)
                break
            results += items
            if len(items) < 100:
                break
        print(f"[CF] '{kw}': {len(results)} notices", flush=True)
        return results

    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            all_items = [n for res in ex.map(search, kws) for n in res]
    except Exception as e:
        print(f"[CF] ThreadPool error: {e}", flush=True)
        all_items = []

    for n in all_items:
        i = n.get("item", {})
        nid = i.get("id")
        if not nid or nid in found:
            continue
        deadline = parse_dt(i.get("deadlineDate"))
        if not live(deadline):
            continue
        raw  = i.get("cpvCodes")
        cpvs = [c.strip() for c in raw.split(",") if c.strip()] if isinstance(raw, str) else (raw or [])
        cats = match(f"{i.get('title','')} {i.get('description','')}", cpvs)
        if not cats:
            continue
        found[nid] = {
            "id": f"CF-{nid}", "source": "Contracts Finder",
            "title": i.get("title"), "buyer": i.get("organisationName"),
            "description": (i.get("description") or "")[:600],
            "published": i.get("publishedDate"),
            "deadline": deadline.isoformat() if deadline else None,
            "value": i.get("valueHigh") or i.get("valueLow"),
            "region": i.get("regionText"), "cpv": cpvs, "categories": cats,
            "url": f"https://www.contractsfinder.service.gov.uk/Notice/{nid}",
        }
    print(f"[CF] {len(found)} matching live tenders", flush=True)
    return list(found.values())


# ── Find a Tender ─────────────────────────────────────────────────────────────

def find_a_tender(days_back=60, max_pages=60):
    since = (NOW - dt.timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    url   = (f"{FTS_BASE}/api/1.0/ocdsReleasePackages"
             f"?stages=tender&limit=100&updatedFrom={since}")
    out, seen = [], set()

    for _ in range(max_pages):
        print(f"[FTS] fetching page, {len(out)} matches so far", flush=True)
        try:
            r = requests.get(url, headers=UA, timeout=90)
            r.raise_for_status()
            pkg = r.json()
        except Exception as e:
            print(f"[FTS] failed: {e}")
            break

        for rel in pkg.get("releases", []):
            t = rel.get("tender", {})
            deadline = parse_dt((t.get("tenderPeriod") or {}).get("endDate"))
            if not live(deadline) or t.get("status") in ("cancelled", "complete", "withdrawn"):
                continue
            cpvs_raw = [t.get("classification", {}).get("id")]
            cpvs_raw += [c.get("id") for c in t.get("additionalClassifications", [])]
            for it in t.get("items", []):
                cpvs_raw.append((it.get("classification") or {}).get("id"))
                cpvs_raw += [c.get("id") for c in it.get("additionalClassifications", [])]
            cpvs = sorted({c for c in cpvs_raw if c})
            cats = match(f"{t.get('title','')} {t.get('description','')}", cpvs)
            m    = (re.search(r"\d{6}-\d{4}", rel.get("id", "")) or
                    re.search(r"\d{6}-\d{4}", rel.get("ocid", "")))
            nid  = m.group(0) if m else rel.get("id")
            if not cats or nid in seen:
                continue
            seen.add(nid)
            out.append({
                "id": f"FTS-{nid}", "source": "Find a Tender",
                "title": t.get("title"), "buyer": (rel.get("buyer") or {}).get("name"),
                "description": (t.get("description") or "")[:600],
                "published": rel.get("date"),
                "deadline": deadline.isoformat() if deadline else None,
                "value": (t.get("value") or {}).get("amount"),
                "region": None, "cpv": cpvs, "categories": cats,
                "url": f"https://www.find-tender.service.gov.uk/Notice/{nid}",
            })

        next_url = (pkg.get("links") or {}).get("next")
        if not next_url:
            break
        url = next_url if next_url.startswith("http") else (
            FTS_BASE + ("" if next_url.startswith("/") else "/") + next_url
        )

    print(f"[FTS] {len(out)} matching live tenders", flush=True)
    return out


# ── ProContract portals (PCS / Sell2Wales / eTendersNI) ──────────────────────

def _parse_atom_entry(entry, source_name, code):
    """Parse one Atom <entry> into a tender dict (or None if invalid)."""
    link_el  = entry.find("a:link",      ATOM_NS)
    title_el = entry.find("a:title",     ATOM_NS)
    sum_el   = entry.find("a:summary",   ATOM_NS)
    pub_el   = entry.find("a:published", ATOM_NS)
    id_el    = entry.find("a:id",        ATOM_NS)

    link  = (link_el.get("href") if link_el is not None else "") or ""
    title = (title_el.text or "").strip() if title_el is not None else ""
    raw   = (sum_el.text or "")           if sum_el   is not None else ""
    desc  = _strip_html(raw)
    pub   = pub_el.text if pub_el is not None else None
    eid   = (id_el.text or link) if id_el is not None else link

    if not title and not link:
        return None, None

    nid = re.sub(r"[^a-zA-Z0-9]", "", eid)[-28:]
    if not nid:
        return None, None

    deadline = _extract_deadline(desc)
    return f"{code}-{nid}", {
        "id":          f"{code}-{nid}",
        "source":      source_name,
        "title":       title,
        "buyer":       _extract_buyer(desc),
        "description": desc[:600],
        "published":   pub,
        "deadline_dt": deadline,          # raw datetime for live() check
        "deadline":    deadline.isoformat() if deadline else None,
        "value":       _extract_value(desc),
        "region":      None,
        "cpv":         [],
        "categories":  [],                # filled after match()
        "url":         link,
    }


def procontract_portal(name: str, base_url: str, code: str, max_pages: int = 4):
    """Fetch keyword-filtered notices from a ProContract portal's Atom feed."""
    feed_base = f"{base_url}/Atom/Atom.aspx"
    all_kws   = sorted({k for c in CATEGORIES.values() for k in c["kw"]})
    raw: dict = {}

    def fetch_kw(kw):
        local = {}
        for page in range(1, max_pages + 1):
            url = (f"{feed_base}?type=1&keyword={requests.utils.quote(kw, safe='')}"
                   + (f"&page={page}" if page > 1 else ""))
            try:
                r = requests.get(url, headers=UA, timeout=60)
                r.raise_for_status()
                root    = ET.fromstring(r.content)
                entries = root.findall("a:entry", ATOM_NS)
            except Exception as e:
                print(f"[{code}] '{kw}' p{page}: {e}", flush=True)
                break
            if not entries:
                break
            for entry in entries:
                nid, data = _parse_atom_entry(entry, name, code)
                if nid and nid not in local:
                    local[nid] = data
            if len(entries) < 10:
                break
        return local

    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for result in ex.map(fetch_kw, all_kws):
                for nid, data in result.items():
                    if nid not in raw:
                        raw[nid] = data
    except Exception as e:
        print(f"[{code}] ThreadPool error: {e}", flush=True)

    # Filter: live deadline + category match
    out = []
    for t in raw.values():
        deadline = t.pop("deadline_dt", None)
        if not live(deadline):
            continue
        cats = match(f"{t.get('title','')} {t.get('description','')}", [])
        if not cats:
            continue
        t["categories"] = cats
        out.append(t)

    print(f"[{code}] {len(out)} matching live tenders", flush=True)
    return out


# ── TED (Tenders Electronic Daily) ───────────────────────────────────────────

def ted_notices():
    """
    Optional source. Requires TED_API_KEY environment variable.
    Free registration at: https://developer.ted.europa.eu
    Add TED_API_KEY as a GitHub Actions secret to use in the workflow.
    """
    api_key = os.environ.get("TED_API_KEY", "")
    if not api_key:
        print("[TED] TED_API_KEY not set – skipping", flush=True)
        return []

    search_url = "https://api.ted.europa.eu/v3/notices/search"
    headers    = {**UA, "Authorization": f"Bearer {api_key}"}
    all_kws    = sorted({k for c in CATEGORIES.values() for k in c["kw"]})
    since_date = (NOW - dt.timedelta(days=60)).strftime("%Y%m%d")
    found: dict = {}

    for kw in all_kws:
        body = {
            "query": f'({kw}) AND publication-date >= {since_date}',
            "fields": ["id", "title", "description", "buyer-name",
                       "deadline-date", "value-estimated", "cpv-code", "notice-url"],
            "limit": 100, "page": 1, "sort": "publication-date:desc",
        }
        try:
            r = requests.post(search_url, json=body, headers=headers, timeout=60)
            r.raise_for_status()
            notices = r.json().get("notices", [])
        except Exception as e:
            print(f"[TED] '{kw}' failed: {e}", flush=True)
            continue

        for n in notices:
            nid = str(n.get("id", ""))
            if not nid or nid in found:
                continue
            deadline = parse_dt(n.get("deadline-date"))
            if not live(deadline):
                continue
            raw_title = n.get("title", {})
            title = (raw_title.get("eng") or next(iter(raw_title.values()), "")
                     if isinstance(raw_title, dict) else str(raw_title))
            raw_desc = n.get("description", {})
            desc  = (raw_desc.get("eng")  or next(iter(raw_desc.values()),  "")
                     if isinstance(raw_desc, dict)  else str(raw_desc))
            cpvs  = [n["cpv-code"]] if n.get("cpv-code") else []
            cats  = match(f"{title} {desc}", cpvs)
            if not cats:
                continue
            found[nid] = {
                "id": f"TED-{nid}", "source": "TED",
                "title": title, "buyer": n.get("buyer-name"),
                "description": desc[:600], "published": None,
                "deadline": deadline.isoformat() if deadline else None,
                "value": n.get("value-estimated"),
                "region": "UK", "cpv": cpvs, "categories": cats,
                "url": (n.get("notice-url") or
                        f"https://ted.europa.eu/udl?uri=TED:NOTICE:{nid}:TEXT:EN:HTML"),
            }

    print(f"[TED] {len(found)} matching live tenders", flush=True)
    return list(found.values())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                prev = {t["id"]: t for t in json.load(f)["tenders"]}
        except Exception:
            pass

    tenders  = []
    scrapers = [contracts_finder, find_a_tender, ted_notices]
    scrapers += [lambda p=p: procontract_portal(*p) for p in PROCONTRACT_PORTALS]

    for fn in scrapers:
        label = getattr(fn, "__name__", str(fn))
        try:
            got = fn()
            print(f"  → {label}: {len(got)} tenders")
            tenders += got
        except Exception as e:
            print(f"  ✗ {label} crashed: {e}")

    # Deduplicate by id
    seen, unique = set(), []
    for t in tenders:
        if t["id"] not in seen:
            seen.add(t["id"])
            unique.append(t)
    tenders = unique

    for t in tenders:
        t["first_seen"] = prev.get(t["id"], {}).get("first_seen", NOW.isoformat())

    tenders.sort(key=lambda t: t["deadline"] or "9999")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": NOW.isoformat(), "tenders": tenders}, f, indent=1)
    print(f"\n✓ Saved {len(tenders)} tenders → {OUT}")


if __name__ == "__main__":
    main()
