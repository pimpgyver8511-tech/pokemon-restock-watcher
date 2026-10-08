"""Einmalige Diagnose: Woran erkennt man 'ausverkauft' im Shopify-HTML (Feenturm vs. lieferbare Shops)?"""
import re
from curl_cffi import requests

s = requests.Session(impersonate="chrome")
URLS = ["https://feenturm.de/products/pokemon-tcg-30th-celebration-booster-bundle-deutsch-jetzt-vorbestellen",
        "https://feenturm.de/products/pokemon-tcg-30th-celebration-elite-trainer-box-englisch-jetzt-vorbestellen",
        "https://geeksheaven.de/products/pokemon-30-jahre-booster-bundle-deutsch",
        "https://tcgworld.nl/products/30th-celebration-booster-bundle"]
for u in URLS:
    try:
        js = s.get(u + ".js", timeout=25).json()
        html = s.get(u, timeout=25).text
    except Exception as e:
        print(u, e); continue
    print("==", u, "| js.available:", js.get("available"))
    for pat in [r'class="[^"]*price--sold-out[^"]*"', r'class="[^"]*sold[-_]out[^"]*"', r'<button[^>]*(?:add|cart|submit)[^>]*>',
                r'"available":\s*(?:true|false)', r'nicht auf Lager[^<]{0,80}', r'Zufallsprinzip[^<]{0,60}', r'verlos[^<]{0,60}',
                r'preorder[^"<]{0,60}', r'"inventory_quantity":\s*-?\d+', r'"inventory_policy":"\w+"']:
        hits = list(dict.fromkeys(re.findall(pat, html, re.I)))[:4]
        if hits:
            print(f"   {pat[:28]:<30} {[h[:140] for h in hits]}")
