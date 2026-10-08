"""Einmalige Diagnose: Warum meldet Feenturm (Shopify) 'available', obwohl ausverkauft?"""
import json, re
from curl_cffi import requests

s = requests.Session(impersonate="chrome")
base = "https://feenturm.de/products/pokemon-tcg-30th-celebration-booster-bundle-deutsch-jetzt-vorbestellen"
js = s.get(base + ".js", timeout=25).json()
print("title:", js.get("title"), "| available:", js.get("available"), "| price:", js.get("price"), "| tags:", js.get("tags"))
for v in js.get("variants", []):
    print("  variant:", {k: v.get(k) for k in ("title", "available", "price", "inventory_management", "inventory_policy", "inventory_quantity", "requires_shipping", "barcode")})
pj = s.get(base + ".json", timeout=25)
print(".json:", pj.status_code, pj.text[:600])
html = s.get(base, timeout=25).text
t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)))
for kw in ("ausverkauft", "sold out", "nicht verfügbar", "vorbestell", "in den warenkorb", "benachrichtig", "notify", "max", "pro kunde"):
    for m in list(re.finditer(kw, t, re.I))[:2]:
        print(f"  [{kw}]", t[max(0, m.start()-120): m.start()+160])
for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
    print("LD:", m.group(1)[:500])
print("availability in html:", re.findall(r'schema\.org/\w+', html)[:10])
# weitere 30-Jahre-Artikel im Shop
sj = s.get("https://feenturm.de/search/suggest.json?q=30th%20celebration&resources[type]=product&resources[limit]=10", timeout=25).json()
for p in sj["resources"]["results"]["products"]:
    print("suggest:", p.get("title"), p.get("available"), p.get("price"))
