"""Einmalige Diagnose: welche Zugangswege zu Smyths Toys funktionieren vom GitHub-Runner aus?"""
import re
from curl_cffi import requests

BASE = "https://www.smythstoys.com"
PID = "264148"
PRODUCT = f"{BASE}/de/de-de/spielzeug/action-spielzeug/pokemon/pokemon-karten/pokemon-karten-top-trainer-box-30-jahre-edition/p/{PID}"
URLS = [
    PRODUCT,
    f"{BASE}/de/de-de/p/{PID}",
    f"{BASE}/de/de-de/search?text=0196214144842",
    f"{BASE}/de/de-de/search/autocomplete/SearchBox?term=top-trainer-box%2030",
    f"{BASE}/de/de-de/search/autocomplete?term=top-trainer-box",
    f"{BASE}/de/de-de/store-pickup/{PID}/pointOfServices",
    f"{BASE}/de/de-de/p/{PID}/stock",
    f"{BASE}/sitemap.xml",
    f"{BASE}/robots.txt",
]
HDR = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7"}


def show(label, r):
    t = r.text
    low = t[:6000].lower()
    marks = [m for m in ("pardon our interruption", "incapsula", "imperva", "captcha", "_abck", "akamai",
                         "cloudflare", "264148", "trainer", "instock", "outofstock", "add-to-cart", "addtocart",
                         '"price"', "algolia", "ld+json") if m in t.lower()]
    print(f"{label}: HTTP {r.status_code}, {len(t)} B, final={r.url[:80]}, marker={marks}")
    print("   ", re.sub(r"\s+", " ", t[:260]))
    for m in re.finditer(r'(?:https?:)?//[\w.-]*(?:api|algolia|search)[\w.-]*\.[a-z]{2,}[^"\'\s<>]*', t[:200000]):
        print("    host:", m.group(0)[:120])
        break


for imp in ("chrome", "safari", "firefox", "edge", "safari_ios", "chrome_android"):
    s = requests.Session(impersonate=imp)
    try:
        show(f"[{imp}] Start", s.get(f"{BASE}/de/de-de", headers=HDR, timeout=25))
        show(f"[{imp}] Produkt (mit Cookies)", s.get(PRODUCT, headers={**HDR, "Referer": f"{BASE}/de/de-de"}, timeout=25))
    except Exception as e:
        print(f"[{imp}] Fehler: {type(e).__name__}: {e}")

s = requests.Session(impersonate="chrome")
for u in URLS:
    try:
        show(u.replace(BASE, ""), s.get(u, headers=HDR, timeout=25))
    except Exception as e:
        print(u, type(e).__name__, e)
