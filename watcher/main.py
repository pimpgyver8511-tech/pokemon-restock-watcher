"""Ein Durchlauf: alle Shops prüfen, mit dem letzten Stand vergleichen, bei Änderungen mailen."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import fetch, mailer, news
from .parse import ORDERABLE, PREORDER, Offer, extract, product_links, shopify_offer

ROOT = Path(__file__).resolve().parent.parent
LABEL = {"in_stock": "lieferbar", "preorder": "vorbestellbar",
         "out_of_stock": "nicht verfügbar", "unknown": "unklar"}
REPORT_EVERY = timedelta(days=7)
BERLIN = ZoneInfo("Europe/Berlin")


@dataclass
class Result:
    url: str
    offer: Offer


# --------------------------------------------------------------------------- Prüfen

def _dump(shop: str, url: str, html: str) -> None:
    """Seiten ohne erkennbares Produkt zur Fehlersuche ablegen (Workflow-Artefakt)."""
    if not (d := os.environ.get("DEBUG_DIR")):
        return
    import hashlib
    import re
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    print(f"  ↳ Debug {shop}: {len(html)} Bytes, Titel: {title.group(1).strip()[:80] if title else '-'!r}, "
          f"JSON-LD: {html.count('application/ld+json')}, '30 Jahre' im Text: {html.lower().count('30 jahre')}, "
          f"Links: {html.count('<a ')}, URL: {url}")
    Path(d).mkdir(parents=True, exist_ok=True)
    name = re.sub(r"\W+", "_", shop) + "_" + hashlib.sha1(url.encode()).hexdigest()[:8] + ".html"
    (Path(d) / name).write_text(f"<!-- {url} -->\n{html}")


def check_url(url: str, product_cfg: dict, shop: str = "") -> Offer | None:
    if "/products/" in url:  # Shopify-Shop: offizielle JSON-Schnittstelle nutzen
        try:
            return shopify_offer(fetch.get_json(url.split("?")[0].rstrip("/") + ".js"), product_cfg)
        except fetch.FetchError:
            pass
    html = fetch.get(url)
    offer = extract(html, product_cfg)
    if offer is None:
        _dump(shop, url, html)
    return offer


def is_marketplace(shop: dict, offer: Offer) -> bool:
    """Angebot eines Fremdhändlers (z. B. MediaMarkt-/Saturn-Marktplatz)?"""
    own = [s.lower() for s in shop.get("seller_any", [])]
    return bool(own and offer.seller and not any(s in offer.seller.lower() for s in own))


def check_shop(shop: dict, product_cfg: dict, known_urls: list[str]) -> tuple[list[Result], list[str], list[str]]:
    """-> (Ergebnisse, Fehler, neu entdeckte Produkt-URLs)"""
    results: list[Result] = []
    errors: list[str] = []
    discovered: list[str] = []
    urls = list(dict.fromkeys(shop.get("urls", []) + known_urls))

    searches = shop.get("search") or []
    searches = [searches] if isinstance(searches, str) else searches
    search_urls = list(dict.fromkeys(t.format(ean=e) for t in searches for e in product_cfg["eans"]))
    for search_url in search_urls:
        try:
            html = fetch.get(search_url)
        except fetch.FetchError as e:
            errors.append(f"Suche: {e}")
            continue
        # Manche Shops leiten bei eindeutiger EAN direkt auf die Produktseite weiter.
        offer = extract(html, product_cfg)
        if offer and (offer.price is not None or offer.source.startswith(("jsonld", "microdata"))):
            results.append(Result(search_url, offer))
        links = product_links(html, search_url, product_cfg)
        if not offer and not links:
            _dump(shop["name"], search_url, html)
        for link in links:
            if link not in urls:
                urls.append(link)
                discovered.append(link)

    for url in urls:
        try:
            offer = check_url(url, product_cfg, shop["name"])
        except fetch.FetchError as e:
            errors.append(f"{url}: {e}")
            continue
        if offer:
            results.append(Result(url, offer))

    kept = []
    for r in results:
        if is_marketplace(shop, r.offer):
            print(f"  ↳ ignoriert (Marktplatz-Händler '{r.offer.seller}'): {r.url}")
        else:
            kept.append(r)
    return kept, errors, discovered


def in_range(offer: Offer, price_cfg: dict) -> bool:
    return offer.price is not None and price_cfg["min"] <= offer.price <= price_cfg["max"]


def best_result(results: list[Result], price_cfg: dict) -> Result | None:
    def rank(r: Result):
        o = r.offer
        return (o.availability in ORDERABLE and in_range(o, price_cfg),
                o.availability in ORDERABLE,
                o.availability != "unknown",
                o.price is not None,
                -(o.price or 0))
    return max(results, key=rank) if results else None


# --------------------------------------------------------------------------- Auswerten

def fmt_price(p: float | None) -> str:
    return f"{p:.2f} €".replace(".", ",") if p is not None else "Preis ?"


def evaluate(cfg: dict, state: dict, shop: dict, best: Result | None, errors: list[str],
             now: datetime) -> list[tuple[int, str]]:
    """Vergleicht mit dem letzten Stand. Liefert (Priorität, Text); 1 = löst Mail aus, 2 = nur Info."""
    events: list[tuple[int, str]] = []
    name = shop["name"]
    st = state["shops"].setdefault(name, {})
    ship = "" if shop.get("ships_to_de") == "yes" else " (Versand nach DE im Warenkorb prüfen)"

    if best is None:
        if errors:
            st["fails"] = st.get("fails", 0) + 1
            st["last_error"] = errors[-1]
            if st["fails"] >= cfg["failure_alert_after"] and not st.get("fail_alerted"):
                st["fail_alerted"] = True
                events.append((1, f"⚠️ {name}: seit {st['fails']} Läufen nicht prüfbar – {errors[-1]}"))
        else:
            # Seite erreichbar, Produkt aber (noch) nicht gelistet.
            st.update(fails=0, fail_alerted=False, last_error=None)
            st.setdefault("status", "not_listed")
        return events

    st.update(fails=0, fail_alerted=False, last_error=None)
    o = best.offer
    was_hit = st.get("hit", False)
    hit = o.availability in ORDERABLE and in_range(o, cfg["price"])
    orderable = o.availability in ORDERABLE
    was_orderable = st.get("status") in ORDERABLE
    what = "VORBESTELLBAR" if o.availability == PREORDER else "VERFÜGBAR"

    if hit and not was_hit:
        events.append((1, f"✅ {name} [{shop['country']}]: {what} für {fmt_price(o.price)}{ship}\n   {best.url}"))
    elif hit and was_hit and st.get("price") != o.price:
        events.append((2, f"💶 {name}: Preis geändert {fmt_price(st.get('price'))} → {fmt_price(o.price)}\n   {best.url}"))
    elif orderable and not was_orderable and not hit and cfg.get("notify_out_of_range"):
        events.append((1, f"🔸 {name} [{shop['country']}]: {what}, aber {fmt_price(o.price)} "
                          f"(außerhalb {fmt_price(cfg['price']['min'])}–{fmt_price(cfg['price']['max'])})\n   {best.url}"))
    elif was_hit and not hit:
        reason = LABEL.get(o.availability, o.availability) if not orderable else f"jetzt {fmt_price(o.price)}"
        events.append((2, f"❌ {name}: nicht mehr im Zielbereich ({reason})"))

    if orderable and not was_orderable:
        st["last_restock"] = now.astimezone(BERLIN).strftime("%d.%m. %H:%M")
    st.update(hit=hit, status=o.availability, price=o.price, url=best.url,
              last_ok=now.isoformat(timespec="minutes"))
    return events


def overview(cfg: dict, state: dict) -> str:
    lines = []
    for shop in cfg["shops"]:
        st = state["shops"].get(shop["name"], {})
        if st.get("fails"):
            status = f"Fehler ({st.get('last_error')})"
        elif st.get("status") == "not_listed" or not st.get("status"):
            status = "nicht gelistet"
        else:
            status = f"{LABEL.get(st['status'], st['status'])}, {fmt_price(st.get('price'))}"
        restock = f", zuletzt verfügbar ab {st['last_restock']}" if st.get("last_restock") else ""
        lines.append(f"- {shop['name']} [{shop['country']}]: {status}{restock}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- Ablauf

def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("shops", {})
    state.setdefault("discovered", {})
    return state


def run(cfg: dict, state: dict, now: datetime, only: str | None = None,
        with_news: bool = True) -> tuple[str | None, str]:
    """Führt einen Durchlauf aus. -> (Betreff oder None, wenn keine Mail nötig; Mail-Text)"""
    events: list[tuple[int, str]] = []
    for shop in cfg["shops"]:
        if only and shop["name"].lower() != only.lower():
            continue
        known = state["discovered"].get(shop["name"], [])
        results, errors, discovered = check_shop(shop, cfg["product"], known)
        if discovered:
            state["discovered"][shop["name"]] = list(dict.fromkeys(known + discovered))[-5:]
        best = best_result(results, cfg["price"])
        o = best.offer if best else None
        print(f"{shop['name']:<22} " + (
            f"{LABEL.get(o.availability)} {fmt_price(o.price)} [{o.source}, Verkäufer: {o.seller or '?'}] {best.url}" if o
            else ("FEHLER: " + "; ".join(errors) if errors else "nicht gelistet")))
        events += evaluate(cfg, state, shop, best, errors, now)

    news_items: list[news.NewsItem] = []
    if with_news and cfg.get("news"):
        news_items, news_errors = news.check(cfg["news"], state, now)
        for e in news_errors:
            print(f"News-Feed-Fehler: {e}")

    hits = [t for p, t in events if p == 1 and t.startswith("✅")]
    urgent = [t for p, t in events if p == 1]
    report_due = now - datetime.fromisoformat(state.get("last_report", "2000-01-01T00:00+00:00")) >= REPORT_EVERY

    body_parts = []
    if events:
        body_parts.append("Änderungen:\n" + "\n".join(t for _, t in sorted(events)))
    if news_items:
        body_parts.append("Neue Restock-/News-Meldungen:\n" + "\n".join(
            f"- {i.title}\n  {i.link}" for i in news_items))
    body_parts.append("Aktueller Stand aller Shops:\n" + overview(cfg, state))
    body_parts.append(f"Preisbereich: {fmt_price(cfg['price']['min'])} – {fmt_price(cfg['price']['max'])} "
                      f"(ohne Versand). Stand: {now.astimezone(BERLIN).strftime('%d.%m.%Y %H:%M')} Uhr")
    body = "\n\n".join(body_parts)

    product = cfg["product"]["name"]
    if hits:
        subject = f"🚨 {product} verfügbar: " + ", ".join(h.split(" [")[0][2:] for h in hits)
    elif urgent:
        subject = f"{product}: {urgent[0].splitlines()[0][:90]}"
    elif news_items:
        subject = f"📰 {product}: {len(news_items)} neue Restock-Meldung(en)"
    elif report_due and not only:
        subject = f"📋 {product}: Wochenübersicht (Watcher läuft)"
    else:
        return None, body
    if not only:
        state["last_report"] = now.isoformat(timespec="minutes")
    return subject, body


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--state", default=os.environ.get("STATE_FILE", str(ROOT / "state" / "state.json")))
    ap.add_argument("--dry-run", action="store_true", help="keine Mail senden, nur ausgeben")
    ap.add_argument("--shop", help="nur diesen Shop prüfen")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--test-mail", action="store_true", help="nur eine Test-Mail schicken")
    args = ap.parse_args(argv)

    if args.test_mail:
        mailer.send("✅ Pokémon-Watcher: Test-Mail", "Die E-Mail-Benachrichtigung funktioniert.")
        print("Test-Mail gesendet.")
        return 0

    cfg = yaml.safe_load(Path(args.config).read_text())
    state_path = Path(args.state)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)

    subject, body = run(cfg, state, now, only=args.shop, with_news=not args.no_news)

    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(f"### {cfg['product']['name']}\n\n{overview(cfg, state)}\n")

    if subject:
        print(f"\n--- Mail: {subject}\n{body}")
        if not args.dry_run:
            try:
                mailer.send(subject, body)
            except Exception as e:
                # Stand NICHT speichern, damit der nächste Lauf die Meldung erneut versucht.
                print(f"Mailversand fehlgeschlagen: {e}", file=sys.stderr)
                return 1
    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
