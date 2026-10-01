import requests, time

H = {"User-Agent": "TenderMonitor/1.0"}

def test(name, fn):
    t = time.time()
    try:
        r = fn()
        print(f"\n[{name}] status {r.status_code} in {round(time.time()-t,1)}s")
        print(r.text[:800])
    except Exception as e:
        print(f"\n[{name}] FAILED after {round(time.time()-t,1)}s: {e!r}")

test("Contracts Finder", lambda: requests.post(
    "https://www.contractsfinder.service.gov.uk/api/rest/2/search_notices/json",
    json={"searchCriteria": {"keyword": "supported living", "statuses": ["Open"]}, "size": 5},
    headers=H, timeout=30))

test("Find a Tender", lambda: requests.get(
    "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?stages=tender&limit=5",
    headers=H, timeout=30))