"""Texterkennung (OCR) auf Prospektseiten.

kaufda erfasst nur einen Teil der Prospektartikel als maschinenlesbare Angebote; der Rest steht
nur im Seitenbild. Für ausgewählte Händler werden die Seitenbilder deshalb einmalig per
Tesseract gelesen und nach Pokémon durchsucht. Ergebnisse werden je Prospekt gemerkt.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import fetch

_IMG_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:$|\?)", re.I)
_PRICE_RE = re.compile(r"(\d{1,3})[,.](\d{2})\s*(?:€|EUR)?|(\d{1,3})\s*[,.]?\s*[-–]\s*€?")


def available() -> bool:
    return shutil.which("tesseract") is not None


def _walk_strings(node, key=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v, key)
    elif isinstance(node, str):
        yield key, node


def page_images(page: dict) -> str | None:
    """Größtes Bild einer Prospektseite (Aufbau der Antwort ist nicht dokumentiert → heuristisch)."""
    candidates = [(k, v) for k, v in _walk_strings(page) if v.startswith("http") and _IMG_RE.search(v)]
    if not candidates:
        return None

    def score(kv):
        k, v = kv
        text = f"{k} {v}".lower()
        size = max([int(n) for n in re.findall(r"(\d{3,4})(?=\D)", v)] or [0])
        return (any(w in text for w in ("zoom", "large", "original", "full", "hd")), size)
    return max(candidates, key=score)[1]


_POKEMON_RE = re.compile(r"p\s?[o0]\s?k\s?[eéèë]\s?m\s?[o0]\s?n", re.I)
INFO: dict = {}  # Diagnose der zuletzt gelesenen Seite (Bildgröße, erkannte Zeichen)


def ocr_image(url: str) -> str:
    data = fetch.get_bytes(url)
    with tempfile.TemporaryDirectory() as d:
        img = Path(d) / "page"
        img.write_bytes(data)
        size = ""
        if shutil.which("identify"):
            size = subprocess.run(["identify", "-format", "%wx%h", str(img)],
                                  capture_output=True, text=True, timeout=30).stdout.strip()
            # Kleine Seitenbilder hochskalieren – Tesseract liest Prospektschrift sonst schlecht.
            w = int(size.split("x")[0]) if size[:1].isdigit() else 0
            if 0 < w < 1800 and shutil.which("convert"):
                big = Path(d) / "page_big.png"
                subprocess.run(["convert", str(img), "-resize", f"{int(2000 / w * 100)}%", str(big)],
                               capture_output=True, timeout=60)
                img = big if big.exists() else img
        out = subprocess.run(["tesseract", str(img), "stdout", "-l", "deu+eng", "--psm", "3"],
                             capture_output=True, text=True, timeout=120)
    INFO.update(size=size, chars=len(out.stdout))
    return out.stdout


def find_pokemon(text: str, product_cfg: dict) -> dict | None:
    t = _POKEMON_RE.sub("Pokémon", re.sub(r"\s+", " ", text))
    low = t.lower()
    if "pokémon" not in low:
        return None
    exact = any(w in low for w in product_cfg["name_any"]) and any(w in low for w in product_cfg["type_any"])
    i = low.find("pokémon")
    snippet = t[max(0, i - 60): i + 140].strip()
    prices = [float(f"{m[0]}.{m[1]}") for m in _PRICE_RE.findall(t[max(0, i - 200): i + 400]) if m[0]]
    return {"exact": exact, "snippet": snippet, "prices": prices[:5]}


def scan(brochure_id: str, publisher: str | None, pages_data: dict, product_cfg: dict,
         max_pages: int = 60) -> list[dict] | None:
    """Treffer je Seite; None, wenn keine Seitenbilder gefunden wurden (dann später erneut versuchen)."""
    hits = []
    pages = (pages_data.get("contents") or [])[:max_pages]
    first = None
    chars = 0
    for n, page in enumerate(pages):
        url = page_images(page)
        first = first or url
        if not url:
            continue
        try:
            found = find_pokemon(ocr_image(url), product_cfg)
        except Exception as e:  # einzelne Seiten dürfen scheitern
            print(f"    OCR Seite {n}: {type(e).__name__}: {e}")
            continue
        chars += INFO.get("chars", 0)
        if n == 0:
            print(f"    Seite 1: {url[:90]} – Bild {INFO.get('size') or '?'}, {INFO.get('chars', 0)} Zeichen erkannt")
        if found:
            hits.append({"page": n, **found})
    if not first and pages:
        import json
        print("    Aufbau Seite 0:", json.dumps(pages[0], ensure_ascii=False)[:700])
    print(f"  OCR {brochure_id[:8]} ({publisher or '?'}): {len(pages)} Seiten, {chars} Zeichen, {len(hits)} mit Pokémon"
          + ("" if first else " – keine Seitenbilder gefunden"))
    return hits if first else None
