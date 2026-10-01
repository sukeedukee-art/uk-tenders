"""UK live-tender collector.
Sources: Contracts Finder API + Find a Tender OCDS API (official, free, no key).
Output: tenders.json (read by index.html). Run daily.
"""
from concurrent.futures import ThreadPoolExecutor
import json, re, datetime as dt, requests, os

UA = {"User-Agent": "TenderMonitor/1.0 (set-your-contact-email@example.com)"}
OUT = "tenders.json"
NOW = dt.datetime.now(dt.timezone.utc)

CATEGORIES = {
    "Supported Living": {
        "kw": ["supported living", "supported housing", "learning disabilit",
               "mental health accommodation", "care and support", "extra care"],
        "cpv": ["85311", "85312", "85310"],
    },
    "Supported Accommodation": {
        "kw": ["supported accommodation", "semi-independent", "16+ accommodation",
               "homeless accommodation", "housing related support", "temporary accommodation"],
        "cpv": ["55", "70"],  # narrowed by keywords below (see match())
    },
    "Construction": {
        "kw": ["construction", "refurbishment", "building works", "groundworks",
               "new build", "civil engineering", "modular"],
        "cpv": ["45"],
    },
    "Teleradiology": {
        "kw": ["teleradiology", "radiology reporting", "diagnostic imaging",
               "remote reporting", "radiology"],
        "cpv": ["85121", "33111", "85120"],
    },
}
# Categories where a CPV match alone is NOT enough (too broad)
KEYWORD_REQUIRED = {"Supported Accommodation"}

# Base URL for FTS pagination — used to resolve relative next-links
FTS_BASE = "https://www.find-tender.service.gov.uk"


def match(text, cpvs):
    text = text.lower()
    hits = []
    for cat, c in CATEGORIES.items():
        kw = any(k in text for k in c["kw"])
        cp = any(code.startswith(p) for code in cpvs for p in c["cpv"])
        if (kw or (cp and cat not in KEYWORD_REQUIRED)) and cat not in hits:
            hits.append(cat)
    return hits


def parse_dt(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def live(deadline):
    return deadline is None or deadline > NOW


# ---------- Contracts Finder ----------
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
        raw = i.get("cpvCodes")
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
    return list(found.values())


# ---------- Find a Tender ----------
def find_a_tender(days_back=60, max_pages=60):
    since = (NOW - dt.timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    url = (f"{FTS_BASE}/api/1.0/ocdsReleasePackages"
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
            # Build CPV list, filtering None values immediately
            cpvs_raw = [t.get("classification", {}).get("id")]
            cpvs_raw += [c.get("id") for c in t.get("additionalClassifications", [])]
            for it in t.get("items", []):
                cpvs_raw.append((it.get("classification") or {}).get("id"))
                cpvs_raw += [c.get("id") for c in it.get("additionalClassifications", [])]
            cpvs = sorted({c for c in cpvs_raw if c})
            cats = match(f"{t.get('title','')} {t.get('description','')}", cpvs)
            m = re.search(r"\d{6}-\d{4}", rel.get("id", "")) or re.search(r"\d{6}-\d{4}", rel.get("ocid", ""))
            nid = m.group(0) if m else rel.get("id")
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
        # Resolve next URL — handle relative links returned by the API
        next_url = (pkg.get("links") or {}).get("next")
        if not next_url:
            break
        if next_url.startswith("http"):
            url = next_url
        else:
            url = FTS_BASE + ("" if next_url.startswith("/") else "/") + next_url
    return out


def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, "r", encoding="utf-8") as f:
                prev = {t["id"]: t for t in json.load(f)["tenders"]}
        except Exception:
            pass
    tenders = []
    for fn in (contracts_finder, find_a_tender):
        try:
            got = fn()
            print(f"{fn.__name__}: {len(got)} matching live tenders")
            tenders += got
        except Exception as e:
            print(f"{fn.__name__} crashed: {e}")
    for t in tenders:
        t["first_seen"] = prev.get(t["id"], {}).get("first_seen", NOW.isoformat())
    tenders.sort(key=lambda t: t["deadline"] or "9999")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": NOW.isoformat(), "tenders": tenders}, f, indent=1)
    print(f"Saved {len(tenders)} tenders -> {OUT}")


if __name__ == "__main__":
    main()
