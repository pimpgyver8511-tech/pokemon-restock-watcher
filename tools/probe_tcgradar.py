"""Einmalige Diagnose: Was liefert tcgradar.eu ohne Login?"""
import json, re
from curl_cffi import requests

s = requests.Session(impersonate="chrome")
H = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"}


def show(u, accept=None):
    try:
        r = s.get(u, headers={**H, **({"Accept": accept} if accept else {})}, timeout=25)
    except Exception as e:
        print(u, type(e).__name__, e); return ""
    t = r.text
    print(f"== {u}: HTTP {r.status_code}, {len(t)} B, {r.headers.get('content-type')}, final={r.url}")
    print("   ", re.sub(r"\s+", " ", t[:300]))
    return t


home = show("https://tcgradar.eu/")
show("https://tcgradar.eu/robots.txt")
show("https://tcgradar.eu/sitemap.xml")
# Hinweise auf APIs, Suchpfade, Produktseiten, Login/Premium, Bedingungen
for pat in [r'https?://[\w.-]*tcgradar[\w./?=&%-]*', r'"/api/[^"]+"', r'/(?:api|graphql|search|suche|product|produkt|deals|angebote|feed|rss)[\w/.-]*',
            r'(?:href|src)="([^"]+\.js)"', r'premium[^<"]{0,80}', r'30[\s-]*(?:jahre|th celebration)[^<"]{0,80}']:
    found = list(dict.fromkeys(re.findall(pat, home, re.I)))[:15]
    print(f"   [{pat[:30]}] {found}")
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', home, re.S)
if m:
    d = json.loads(m.group(1)); print("   NEXT keys:", list((d.get("props") or {}).get("pageProps") or {})[:20], "buildId", d.get("buildId"))
if "__NUXT" in home: print("   Nuxt-App")
for u in ["https://tcgradar.eu/api/products?search=30", "https://tcgradar.eu/api/deals", "https://tcgradar.eu/feed",
          "https://tcgradar.eu/search?q=30%20Jahre%20Top-Trainer-Box", "https://tcgradar.eu/agb", "https://tcgradar.eu/terms",
          "https://tcgradar.eu/nutzungsbedingungen", "https://tcgradar.eu/impressum"]:
    t = show(u, "application/json, text/html;q=0.9")
    for kw in ("automat", "scrap", "crawl", "bot", "robot", "api"):
        for mm in list(re.finditer(kw, re.sub(r"<[^>]+>", " ", t), re.I))[:2]:
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))
            i = mm.start()
print("fertig")
