"""Einmalige Diagnose: tcgradar.eu – Bedingungen, 30-Jahre-Seiten, Tracker."""
import json, re
from curl_cffi import requests

s = requests.Session(impersonate="chrome")
H = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"}
txt = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)))

terms = txt(s.get("https://tcgradar.eu/en/terms", headers=H, timeout=25).text)
for kw in ("automat", "scrap", "crawl", "bot", "robot", "api", "account", "shar", "commercial", "personal"):
    for m in list(re.finditer(kw, terms, re.I))[:2]:
        print(f"[terms:{kw}]", terms[max(0, m.start()-200): m.start()+250])

sm = s.get("https://tcgradar.eu/sitemap.xml", headers=H, timeout=25).text
locs = re.findall(r"<loc>([^<]+)</loc>", sm)
print("sitemap:", len(locs), "URLs; Beispiele:", locs[1:12])
hits = [u for u in locs if re.search(r"30|celebration|jahre|trainer", u, re.I)]
print("30-Jahre-URLs:", hits[:30])

for u in (hits[:4] + ["https://tcgradar.eu/de/tracker?q=30%20Jahre%20Top-Trainer-Box", "https://tcgradar.eu/de/tracker?q=0196214144842"]):
    r = s.get(u, headers=H, timeout=25)
    h = r.text
    print(f"\n== {u}: HTTP {r.status_code}, {len(h)} B")
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        print("   LD:", m.group(1)[:600])
    t = txt(h)
    i = max(0, t.lower().find("trainer"))
    print("   Text:", t[i-200: i+1500])
    print("   €-Preise:", re.findall(r"\d{1,3}[,.]\d{2}\s?€|€\s?\d{1,3}[,.]\d{2}", t)[:20])
    print("   Premium/Login-Hinweise:", re.findall(r"[^.]{0,60}(?:premium|log ?in|anmelden|sign in)[^.]{0,60}", t, re.I)[:5])
    # Daten im Next.js-RSC-Stream
    rsc = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', h, re.S)
    blob = "".join(rsc)
    for kw in ("price", "inStock", "in_stock", "retailer", "store", "availability"):
        j = blob.find(kw)
        if j >= 0:
            print(f"   RSC[{kw}]:", blob[max(0, j-150): j+350].replace('\\"', '"')[:500])
            break
