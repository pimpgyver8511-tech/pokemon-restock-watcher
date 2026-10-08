"""Einmalige Diagnose: Welche Such-URL funktioniert je Shop? (Text- und EAN-Suche)"""
import yaml
from urllib.parse import quote_plus
from watcher import fetch
from watcher.parse import product_links

cfg = yaml.safe_load(open("config.yaml"))
bundle = {"eans": [], "exclude_any": [], **cfg["more_products"][0]}
ttb = cfg["product"]
PATTERNS = ["/search?q={q}", "/search?sSearch={q}", "/search?search={q}", "/suche?q={q}", "/catalogsearch/result/?q={q}",
            "/?s={q}&post_type=product", "/?qs={q}", "/navi.php?qs={q}", "/index.php?controller=search&s={q}",
            "/en/search?q={q}", "/de/search?q={q}", "/de/suche?q={q}", "/shop/search?q={q}", "/de/search?sSearch={q}"]
for shop in cfg["shops"]:
    if shop.get("search") or shop.get("aggregator") or not shop.get("urls") or "/products/" in shop["urls"][0]:
        continue
    base = "/".join(shop["urls"][0].split("/")[:3])
    found = None
    for pat in PATTERNS:
        url = base + pat.format(q=quote_plus("30 Jahre Booster Bundle"))
        try:
            html = fetch.get(url, timeout=15)
        except Exception as e:
            continue
        links = product_links(html, url, bundle)
        if links:
            ean_url = base + pat.format(q="0196214144842")
            try:
                ean_links = product_links(fetch.get(ean_url, timeout=15), ean_url, ttb)
            except Exception:
                ean_links = []
            found = (pat, links[:2], ean_links[:2])
            break
    print(f"{shop['name']:<26} {found}")
