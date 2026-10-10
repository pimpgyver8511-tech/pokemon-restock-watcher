"""Ein Durchlauf: alle Shops prüfen, mit dem letzten Stand vergleichen, bei Änderungen mailen."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import yaml

from . import fetch, flyers, mailer, news, shopify, stores
from .parse import IN_STOCK, ORDERABLE, OUT_OF_STOCK, PREORDER, Offer, extract, is_asset, product_links, shopify_offer

ROOT = Path(__file__).resolve().parent.parent
LABEL = {"in_stock": "lieferbar", "preorder": "vorbestellbar",
         "out_of_stock": "nicht verfügbar", "unknown": "unklar"}
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


# Verlosung statt Verkauf (z. B. Feenturm): Shop meldet "verfügbar", kaufen kann man aber nicht.
_RAFFLE = re.compile(r"per zufallsprinzip|wird nicht direkt gekauft|über eine verlosung|verlosung \(raffle\)"
                     r"|enter the raffle|raffle entry", re.I)
_CHROME = re.compile(r"<(header|footer|nav)\b.*?</\1>", re.I | re.S)  # Menüs/Fußzeile nicht werten
_PREORDER_NOTE = re.compile(r"nicht auf lager,? kann aber vorbestellt werden", re.I)


def page_check(offer: Offer, html: str, url: str) -> Offer:
    """Hinweise auf der Produktseite, die die gemeldete Verfügbarkeit einschränken."""
    if offer.availability in ORDERABLE and _RAFFLE.search(_CHROME.sub(" ", html)):
        print(f"  ↳ nur Verlosung, nicht direkt kaufbar: {url}")
        offer.availability = OUT_OF_STOCK
    elif offer.availability == IN_STOCK and _PREORDER_NOTE.search(html):
        offer.availability = PREORDER
    return offer


def check_url(url: str, product_cfg: dict, shop: str = "") -> Offer | None:
    if "/products/" in url:  # Shopify-Shop: offizielle JSON-Schnittstelle nutzen
        try:
            offer = shopify_offer(fetch.get_json(url.split("?")[0].rstrip("/") + ".js"), product_cfg)
        except fetch.FetchError:
            offer = None
        if offer is not None:
            if offer.availability in ORDERABLE:  # "available" kann von Vorbestell-/Verlosungs-Apps stammen
                try:
                    offer = page_check(offer, fetch.get(url), url)
                except fetch.FetchError:
                    pass
            return offer
    html = fetch.get(url)
    offer = extract(html, product_cfg)
    if offer is None:
        _dump(shop, url, html)
        return None
    return page_check(offer, html, url)


def is_marketplace(shop: dict, offer: Offer) -> bool:
    """Angebot eines Fremdhändlers (z. B. MediaMarkt-/Saturn-Marktplatz)?"""
    own = [s.lower() for s in shop.get("seller_any", [])]
    return bool(own and offer.seller and not any(s in offer.seller.lower() for s in own))


def check_shop(shop: dict, product_cfg: dict, known_urls: list[str]) -> tuple[list[Result], list[str], list[str]]:
    """-> (Ergebnisse, Fehler, neu entdeckte Produkt-URLs)"""
    results: list[Result] = []
    errors: list[str] = []
    discovered: list[str] = []
    urls = list(dict.fromkeys(shop.get("urls", []) + [u for u in known_urls if not is_asset(u)]))

    searches = shop.get("search") or []
    searches = [searches] if isinstance(searches, str) else searches
    terms = list(product_cfg["eans"]) + [quote_plus(t) for t in product_cfg.get("search_terms", [])]
    if not product_cfg.get("main", True):  # feste Seiten (ohne {ean}) gehören zur Hauptbox
        searches = [t for t in searches if "{ean}" in t]
    search_urls = list(dict.fromkeys(t.format(ean=e) for t in searches for e in terms))
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

    if shop.get("aggregator"):
        # Preisvergleiche listen nur verfügbare Angebote; ein Preis heißt: irgendwo bestellbar.
        for r in results:
            if r.offer.price is not None and r.offer.availability == "unknown":
                r.offer.availability = "in_stock"
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


def state_key(shop: dict, product: dict | None) -> str:
    """Stand je Shop; weitere Produkte (z. B. Booster-Bundle) unter eigenem Schlüssel."""
    return shop["name"] if not product or product.get("main", True) else f"{product['id']}|{shop['name']}"


def evaluate(cfg: dict, state: dict, shop: dict, best: Result | None, errors: list[str],
             now: datetime, product: dict | None = None) -> list[tuple[int, str]]:
    """Vergleicht mit dem letzten Stand. Liefert (Priorität, Text); 1 = löst Mail aus, 2 = nur Info."""
    events: list[tuple[int, str]] = []
    extra = product is not None and not product.get("main", True)
    price_cfg = product["price"] if extra else cfg["price"]
    name = f"{shop['name']} ({product.get('short') or product['name']})" if extra else shop["name"]
    st = state["shops"].setdefault(state_key(shop, product), {})
    ship = "" if shop.get("ships_to_de") == "yes" else " (Versand nach DE im Warenkorb prüfen)"

    if best is None:
        if errors:
            st["fails"] = st.get("fails", 0) + 1
            st["last_error"] = errors[-1]
            if st["fails"] >= cfg["failure_alert_after"] and not st.get("fail_alerted"):
                st["fail_alerted"] = True
                events.append((1, f"⚠️ {name}: seit {st['fails']} Läufen nicht prüfbar – {errors[-1]}"))
        else:
            # Seite erreichbar, Produkt aber (nicht mehr) gelistet.
            if st.get("hit"):
                events.append((2, f"❌ {name}: nicht mehr im Zielbereich (nicht mehr gelistet)"))
            st.update(fails=0, fail_alerted=False, last_error=None, status="not_listed",
                      hit=False, price=None, url=None, available_from=None,
                      last_ok=now.isoformat(timespec="minutes"))
        return events

    st.update(fails=0, fail_alerted=False, last_error=None)
    o = best.offer
    was_hit = st.get("hit", False)
    # Preisvergleiche zeigen oft Einladungs-/Altpreise (z. B. Amazon 52,99 €) – nie als Treffer werten.
    hit = o.availability in ORDERABLE and in_range(o, price_cfg) and not shop.get("aggregator")
    orderable = o.availability in ORDERABLE
    was_orderable = st.get("status") in ORDERABLE
    what = "VORBESTELLBAR" if o.availability == PREORDER else "VERFÜGBAR"
    if o.available_from:
        what += f" (Lieferung ab {o.available_from})"

    if hit and not was_hit:
        events.append((1, f"✅ {name} [{shop['country']}]: {what} für {fmt_price(o.price)}{ship}\n   {best.url}"))
    elif hit and was_hit and st.get("price") != o.price:
        events.append((2, f"💶 {name}: Preis geändert {fmt_price(st.get('price'))} → {fmt_price(o.price)}\n   {best.url}"))
    elif orderable and not was_orderable and not hit and cfg.get("notify_out_of_range"):
        events.append((1, f"🔸 {name} [{shop['country']}]: {what}, aber {fmt_price(o.price)} "
                          f"(außerhalb {fmt_price(price_cfg['min'])}–{fmt_price(price_cfg['max'])})\n   {best.url}"))
    elif was_hit and not hit:
        reason = LABEL.get(o.availability, o.availability) if not orderable else f"jetzt {fmt_price(o.price)}"
        events.append((2, f"❌ {name}: nicht mehr im Zielbereich ({reason})"))

    if orderable and not was_orderable:
        st["last_restock"] = now.astimezone(BERLIN).strftime("%d.%m. %H:%M")
    st.update(hit=hit, status=o.availability, price=o.price, url=best.url, available_from=o.available_from,
              last_ok=now.isoformat(timespec="minutes"))
    return events


def shop_link(shop: dict, product_cfg: dict) -> str | None:
    """Link, wenn kein konkretes Angebot bekannt ist: bekannte Produktseite oder Shop-Suche."""
    if shop.get("urls"):
        return shop["urls"][0]
    searches = shop.get("search") or []
    searches = [searches] if isinstance(searches, str) else searches
    terms = list(product_cfg["eans"]) + [quote_plus(t) for t in product_cfg.get("search_terms", [])]
    searches = [t for t in searches if "{ean}" in t or product_cfg.get("main", True)]
    return searches[0].format(ean=terms[0]) if searches and terms else None


def extra_products(cfg: dict) -> list[dict]:
    """Weitere überwachte Produkte (config: more_products), z. B. Booster-Bundle."""
    out = []
    for p in cfg.get("more_products") or []:
        out.append({"eans": [], "exclude_any": [], **p, "main": False})
    return out


def extra_shops(cfg: dict, product: dict, state: dict) -> list[dict]:
    """Shops für ein weiteres Produkt: bekannte Produktseiten, EAN-/Textsuche und Shopify-Suche."""
    known = product.get("urls") or {}
    shops = []
    for shop in cfg["shops"]:
        if shop.get("discovered"):
            continue
        searches = shop.get("search") or []
        searches = [searches] if isinstance(searches, str) else searches
        urls = known.get(shop["name"], [])
        if urls or any("{ean}" in t for t in searches):
            shops.append({**shop, "urls": urls})
    if (sq := cfg.get("shopify_search")) and product.get("shopify_queries"):
        hosts = {u.split("/")[2] for s in cfg["shops"] for u in s.get("urls", []) if "/products/" in u}
        sub = {"queries": product["shopify_queries"],
               "domains": list(dict.fromkeys(sq.get("domains", []) + sorted(hosts)))}
        names = {s["name"] for s in shops}
        for v in shopify.discover(sub, [], product, state, key=f"shopify_found_{product['id']}"):
            # gleicher Laden wie ein konfigurierter Shop → unter dessen Namen führen
            same = next((s for s in cfg["shops"] if any(v["name"] in u for u in s.get("urls", []))), None)
            if same and same["name"] in names:
                next(s for s in shops if s["name"] == same["name"])["urls"] += v["urls"]
            elif same:
                shops.append({**same, "urls": v["urls"]})
                names.add(same["name"])
            else:
                shops.append(v)
    return shops


def shop_entry(cfg: dict, state: dict, shop: dict, product: dict | None = None) -> dict:
    st = state["shops"].get(state_key(shop, product), {})
    status = st.get("status") or "not_listed"
    main_product = product is None or product.get("main", True)
    return {
        "name": shop["name"], "country": shop["country"],
        "product": "main" if main_product else product["id"],
        "aggregator": bool(shop.get("aggregator")), "discovered": bool(shop.get("discovered")),
        "ships_to_de": shop.get("ships_to_de") == "yes",
        "status": status, "price": st.get("price"),
        "in_range": bool(st.get("hit")),
        "url": st.get("url") if status != "not_listed" and st.get("url") else None,
        "shop_url": shop_link(shop, cfg["product"] if main_product else product),
        "error": st.get("last_error") if st.get("fails") else None,
        "available_from": st.get("available_from"),
        "last_ok": st.get("last_ok"), "last_restock": st.get("last_restock"),
    }


def snapshot(cfg: dict, state: dict, now: datetime) -> dict:
    """Stand für die Web-Oberfläche (status.json)."""
    shops = [shop_entry(cfg, state, shop) for shop in cfg["shops"]]
    for product in extra_products(cfg):
        shops += [shop_entry(cfg, state, shop, product) for shop in state.get(f"shops_{product['id']}", [])]
    return {
        "checked_at": now.isoformat(timespec="seconds"),
        "interval_min": (cfg.get("schedule") or {}).get("interval_minutes", 20),
        "product": cfg["product"]["name"],
        "price": cfg["price"],
        "products": [{"id": "main", "name": cfg["product"]["name"], "short": cfg["product"].get("short", "Top-Trainer-Box"),
                      "price": cfg["price"]}]
                    + [{"id": p["id"], "name": p["name"], "short": p.get("short", p["name"]), "price": p["price"]}
                       for p in extra_products(cfg)],
        "shops": shops,
        "news": state.get("news_recent", []),
        "stores": state.get("store_entries", []),
        "flyers": state.get("flyer_offers", []),
        "flyers_area": cfg["flyers"].get("postal_code") if cfg.get("flyers") else None,
        "trips": cfg.get("trips") or [],
        "stores_area": ({"postal_code": cfg["stores"].get("postal_code"), "radius_km": cfg["stores"].get("radius_km")}
                        if cfg.get("stores") else None),
    }


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
            if st.get("available_from"):
                status += f", Lieferung ab {st['available_from']}"
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
    # Altes ISO-Format stammt aus dem ersten Lauf vor dem Marktplatz-Filter und ist teils falsch.
    for st in state["shops"].values():
        if "T" in (st.get("last_restock") or ""):
            del st["last_restock"]
    return state


def run(cfg: dict, state: dict, now: datetime, only: str | None = None,
        with_news: bool = True) -> tuple[str | None, str]:
    """Führt einen Durchlauf aus. -> (Betreff oder None, wenn keine Mail nötig; Mail-Text)"""
    events: list[tuple[int, str]] = []
    if cfg.get("shopify_search") and not only:
        cfg["shops"] = [s for s in cfg["shops"] if not s.get("discovered")]
        cfg["shops"] += shopify.discover(cfg["shopify_search"], cfg["shops"], cfg["product"], state)
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

    for product in extra_products(cfg):
        shops = extra_shops(cfg, product, state) if not only else [
            s for s in extra_shops(cfg, product, state) if s["name"].lower() == only.lower()]
        if not only:
            state[f"shops_{product['id']}"] = shops
        for shop in shops:
            key = state_key(shop, product)
            known = state["discovered"].get(key, [])
            results, errors, discovered = check_shop(shop, product, known)
            if discovered:
                state["discovered"][key] = list(dict.fromkeys(known + discovered))[-5:]
            best = best_result(results, product["price"])
            o = best.offer if best else None
            print(f"[{product.get('short', product['id'])}] {shop['name']:<22} " + (
                f"{LABEL.get(o.availability)} {fmt_price(o.price)} [{o.source}] {best.url}" if o
                else ("FEHLER: " + "; ".join(errors) if errors else "nicht gelistet")))
            events += evaluate(cfg, state, shop, best, errors, now, product)

    if cfg.get("stores") and not only:
        entries, store_errors = stores.check(cfg["stores"], cfg["product"], state)
        for e in store_errors:
            print(f"Filial-Fehler: {e}")
        prev = state.get("store_status", {})
        for e in entries:
            key = f"{e['chain']}|{e['id']}|{e.get('product')}"
            if e["status"] == "in_stock" and prev.get(key) != "in_stock":
                events.append((1, f"✅ {e['chain']} {e['name']} ({e['distance_km']} km) [Filiale]: "
                                  f"SOFORT ABHOLBAR – {e.get('product')}\n   {e.get('url') or ''}"))
            print(f"  Filiale {e['chain']} {e['name']} ({e['distance_km']} km): {e['status']} {e.get('raw') or ''}")
        if entries or not store_errors:
            state["store_status"] = {f"{e['chain']}|{e['id']}|{e.get('product')}": e["status"] for e in entries}
            state["store_entries"] = entries

    if cfg.get("flyers") and not only:
        offers, flyer_errors = flyers.check(cfg["flyers"], cfg["product"], state, extra_products(cfg))
        prices = {p["id"]: p["price"] for p in extra_products(cfg)}
        for e in flyer_errors:
            print(f"Prospekt-Fehler: {e}")
        today = now.astimezone(BERLIN).strftime("%Y-%m-%d")
        seen = set(state.get("flyer_seen", []))
        for o in offers:
            current = not o.get("valid_until") or o["valid_until"] >= today
            print(f"  Prospekt {o['kind']}: {o.get('store')} – {o.get('title')} {o.get('price')} € "
                  f"({o.get('valid_from')}–{o.get('valid_until')})")
            pr = prices.get(o.get("product"), cfg["price"])
            if (o["kind"] == "exact" and current and o.get("price") is not None
                    and pr["min"] <= o["price"] <= pr["max"] and o["id"] not in seen):
                period = f"{o.get('valid_from') or '?'} bis {o.get('valid_until') or '?'}"
                events.append((1, f"✅ {o.get('store')} [Prospekt Leipzig]: {o.get('title')} für "
                                  f"{fmt_price(o['price'])} (gültig {period})\n   {o.get('url') or ''}"))
                seen.add(o["id"])
        if offers or not flyer_errors:
            state["flyer_offers"] = [o for o in offers if not o.get("valid_until") or o["valid_until"] >= today][:80]
            state["flyer_seen"] = sorted(seen)[-300:]

    news_items: list[news.NewsItem] = []
    if with_news and cfg.get("news"):
        news_items, news_errors = news.check(cfg["news"], state, now)
        recent = state.setdefault("news_recent", [])
        recent[:0] = [{"title": i.title, "link": i.link, "source": i.source,
                       "published": (i.published or now).isoformat(timespec="minutes")} for i in news_items]
        seen_news: set[str] = set()  # gleiche Meldung über mehrere Feeds nur einmal

        def first(n):
            keys = {n["link"], n["title"].strip().lower()}
            dup = bool(keys & seen_news)
            seen_news.update(keys)
            return not dup
        recent[:] = [n for n in recent if news.relevant_title(n["title"], cfg["news"]) and first(n)][:30]
        for e in news_errors:
            print(f"News-Feed-Fehler: {e}")

    local = now.astimezone(BERLIN)
    stamp = local.strftime("%d.%m. %H:%M")
    instant = cfg.get("instant_alerts", True)
    hits = [t for p, t in events if p == 1 and t.startswith("✅")]
    pending: list[str] = state.setdefault("pending", [])
    pending += [f"[{stamp}] {t}" for p, t in sorted(events) if not (instant and t in hits)]
    pending += [f"[{stamp}] 📰 {i.title}\n   {i.link}" for i in news_items]

    today = local.strftime("%Y-%m-%d")
    digest_due = (not only and state.get("last_digest") != today
                  and local.hour >= cfg.get("digest_hour", 8))
    alert_now = bool(hits) and instant
    if not (alert_now or digest_due):
        return None, ""

    body_parts = []
    if alert_now:
        body_parts.append("JETZT VERFÜGBAR – schnell sein:\n" + "\n".join(hits))
    if digest_due:
        body_parts.append("Seit der letzten Tagesmail:\n" + ("\n".join(pending) or "Keine neuen Meldungen."))
    body_parts.append("Aktueller Stand aller Shops:\n" + overview(cfg, state))
    body_parts.append(f"Preisbereich: {fmt_price(cfg['price']['min'])} – {fmt_price(cfg['price']['max'])} "
                      f"(ohne Versand). Stand: {local.strftime('%d.%m.%Y %H:%M')} Uhr")

    product = cfg["product"]["name"]
    if alert_now:
        subject = f"🚨 {product} verfügbar: " + ", ".join(h.split(" [")[0][2:] for h in hits)
    else:
        n = len(pending)
        subject = f"📋 {product}: Tagesübersicht {local.strftime('%d.%m.')}" + (
            f" – {n} neue Meldung(en)" if n else " – nichts Neues")
    if digest_due:
        state["last_digest"] = today
        pending.clear()
    return subject, "\n\n".join(body_parts)


def apply_overrides(cfg: dict, env=os.environ) -> dict:
    """Einstellungen aus der Web-Oberfläche (GitHub-Variablen) überschreiben config.yaml."""
    def num(name, cast=float):
        try:
            return cast(env[name]) if env.get(name, "").strip() else None
        except ValueError:
            print(f"Ungültiger Wert für {name}: {env[name]!r} – ignoriert")
            return None
    sched = cfg.setdefault("schedule", {})
    for key, name, cast in [("interval_minutes", "WATCH_INTERVAL_MIN", int),
                            ("active_from", "WATCH_FROM_HOUR", int), ("active_to", "WATCH_TO_HOUR", int)]:
        if (v := num(name, cast)) is not None:
            sched[key] = v
    if (v := num("DIGEST_HOUR", int)) is not None:
        cfg["digest_hour"] = v
    if (v := num("PRICE_MIN")) is not None:
        cfg["price"]["min"] = v
    if (v := num("PRICE_MAX")) is not None:
        cfg["price"]["max"] = v
    if env.get("INSTANT_ALERTS", "").strip():
        cfg["instant_alerts"] = env["INSTANT_ALERTS"].strip().lower() in ("1", "true", "ja", "yes", "on")
    return cfg


def is_due(cfg: dict, state: dict, now: datetime) -> tuple[bool, str]:
    """Soll dieser (geplante) Lauf prüfen? Berücksichtigt Intervall und aktives Zeitfenster."""
    sched = cfg.get("schedule", {})
    start, end = sched.get("active_from", 0), sched.get("active_to", 24)
    hour = now.astimezone(BERLIN).hour
    inside = start <= hour < end if start <= end else (hour >= start or hour < end)  # auch über Mitternacht
    if start != end and not inside:
        return False, f"außerhalb des Zeitfensters {start}–{end} Uhr"
    interval = sched.get("interval_minutes", 20)
    if last := state.get("last_check"):
        elapsed = (now - datetime.fromisoformat(last)).total_seconds() / 60
        # 3 Minuten Toleranz, weil GitHub geplante Läufe ungenau startet.
        if elapsed < interval - 3:
            return False, f"letzte Prüfung vor {elapsed:.0f} min, Intervall {interval} min"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--state", default=os.environ.get("STATE_FILE", str(ROOT / "state" / "state.json")))
    ap.add_argument("--dry-run", action="store_true", help="keine Mail senden, nur ausgeben")
    ap.add_argument("--shop", help="nur diesen Shop prüfen")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--test-mail", action="store_true", help="nur eine Test-Mail schicken")
    ap.add_argument("--force", action="store_true", help="Intervall/Zeitfenster ignorieren (manueller Start)")
    args = ap.parse_args(argv)

    if args.test_mail:
        mailer.send("✅ Pokémon-Watcher: Test-Mail", "Die E-Mail-Benachrichtigung funktioniert.")
        print("Test-Mail gesendet.")
        return 0

    cfg = apply_overrides(yaml.safe_load(Path(args.config).read_text()))
    state_path = Path(args.state)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)

    if not args.force and not args.shop:
        due, reason = is_due(cfg, state, now)
        if not due:
            print(f"Übersprungen: {reason}")
            return 0
    if not args.dry_run and not args.shop:
        state["last_check"] = now.isoformat(timespec="seconds")

    subject, body = run(cfg, state, now, only=args.shop, with_news=not args.no_news)

    if (status_file := os.environ.get("STATUS_FILE")) and not args.shop:
        Path(status_file).parent.mkdir(parents=True, exist_ok=True)
        Path(status_file).write_text(json.dumps(snapshot(cfg, state, now), indent=1, ensure_ascii=False))

    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(f"### {cfg['product']['name']}\n\n{overview(cfg, state)}\n")

    # Schalter der Web-Oberfläche (Variable EMAIL_ENABLED) hat Vorrang vor config.yaml.
    env_mail = os.environ.get("EMAIL_ENABLED", "").strip().lower()
    mail_on = (env_mail not in ("false", "0", "nein", "no", "off")) if env_mail else cfg.get("email_enabled", True)
    if subject:
        print(f"\n--- Mail: {subject}\n{body}")
        if not mail_on:
            print("(E-Mails sind in der Web-Oberfläche abgeschaltet – nicht versendet)")
        elif not args.dry_run:
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
