"""Einmalige Diagnose: Mäc-Geiz-Prospekt ab 26.10. – welche Quelle ist lesbar?"""
import json, re, yaml
from urllib.parse import quote
from watcher import fetch, flyers

cfg = yaml.safe_load(open("config.yaml"))
fc, prod = cfg["flyers"], cfg["product"]
txt = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)))
# kaufda: andere Standorte, wo Mäc-Geiz gelistet ist
for name, lat, lng in [("Chemnitz", 50.8278, 12.9214), ("Leipzig", 51.3397, 12.3731), ("Halle", 51.4825, 11.9697)]:
    data = flyers._json(f"{flyers.API}?query={quote('Mäc-Geiz')}&lat={lat}&lng={lng}&offset=0&limit=48")
    bs = ((data.get("searchResults") or {}).get("contents") or {}).get("brochures") or []
    print(name, [((b.get('content') or {}).get('publisher') or {}).get('name') for b in bs])
for url in ["https://www.kaufda.de/Chemnitz/Maec-Geiz/p-r51", "https://www.kaufda.de/Leipzig/Maec-Geiz/p-r51"]:
    try:
        print(url, flyers.publisher_brochures(url))
    except Exception as e:
        print(url, e)
for url in ["https://www.discounto.de/Prospekte/Anbieter:Mac-Geiz/naechste-woche/",
            "https://www.discounto.de/Prospekte/Anbieter:Mac-Geiz/",
            "https://www.prospektangebote.de/geschaefte/mac-geiz/prospekte-angebote",
            "https://www.prospektangebote.de/robots.txt", "https://www.discounto.de/robots.txt",
            "https://www.maec-geiz.de/", "https://www.maec-geiz.de/prospekt"]:
    try:
        h = fetch.get(url)
    except Exception as e:
        print("==", url, e); continue
    t = txt(h)
    print("==", url, len(h), "B")
    if "robots" in url:
        print("   ", h[:600].replace("\n", " | ")); continue
    for m in list(re.finditer(r"pok[eé]mon", t, re.I))[:6]:
        print("   [pokemon]", t[max(0, m.start()-150): m.start()+200])
    for m in list(re.finditer(r"26\.10|gültig|ab \d\d\.\d\d", t, re.I))[:4]:
        print("   [datum]", t[max(0, m.start()-80): m.start()+120])
    imgs = list(dict.fromkeys(re.findall(r'https?://[^"\'\s>]+\.(?:jpe?g|png|webp)', h)))
    print("   Bilder:", len(imgs), imgs[:4])
    pdfs = re.findall(r'https?://[^"\'\s>]+\.pdf', h)
    print("   PDFs:", pdfs[:4])
