import requests, time
url = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?stages=tender&limit=5"
t = time.time()
try:
    r = requests.get(url, headers={"User-Agent": "TenderMonitor/1.0"}, timeout=30)
    print("Status:", r.status_code, "| seconds:", round(time.time() - t, 1))
    print(r.text[:1500])
except Exception as e:
    print("FAILED after", round(time.time() - t, 1), "s:", repr(e))