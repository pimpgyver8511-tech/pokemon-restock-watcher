"""Einmalige Diagnose: Pokémon Center (EU) vom Runner aus."""
import re
from curl_cffi import requests

H = {"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"}
txt = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)))
URLS = ["https://www.pokemoncenter.com/robots.txt",
        "https://www.pokemoncenter.com/de-de",
        "https://www.pokemoncenter.com/en-gb",
        "https://www.pokemoncenter.com/de-de/search/30%20jahre",
        "https://www.pokemoncenter.com/en-gb/search/30th%20celebration",
        "https://www.pokemoncenter.com/en-gb/category/elite-trainer-box",
        "https://www.pokemoncenter.com/tpci-ecommweb-api/search?q=30th%20celebration&locale=en-gb",
        "https://www.pokemoncenter.com/de-de/terms-of-use",
        "https://www.pokemoncenter.com/en-gb/terms-of-use"]
for imp in ("chrome", "safari"):
    s = requests.Session(impersonate=imp)
    for u in URLS:
        try:
            r = s.get(u, headers=H, timeout=25)
        except Exception as e:
            print(f"[{imp}] {u}: {type(e).__name__} {e}"); continue
        t = r.text
        low = t.lower()
        marks = [m for m in ("incapsula", "imperva", "pardon our interruption", "queue-it", "captcha", "akamai",
                             "access denied", "cloudflare", "just a moment", "__next_data__", "ld+json", "30th", "30 jahre")
                 if m in low]
        print(f"[{imp}] {u}: HTTP {r.status_code}, {len(t)} B, final={r.url[:80]}, {marks}")
        print("     ", txt(t)[:250])
        if "robots" in u:
            print("     ", t[:900].replace("\n", " | "))
        if "terms" in u and r.status_code == 200:
            T = txt(t)
            for kw in ("robot", "spider", "scrap", "automated", "crawl", "data mining", "bot"):
                for m in list(re.finditer(kw, T, re.I))[:2]:
                    print(f"      [terms:{kw}]", T[max(0, m.start()-250): m.start()+250])
    if imp == "chrome":
        print("=" * 40)
