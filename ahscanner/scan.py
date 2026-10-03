"""AH-Scan: Snapshot holen, pro Item verdichten und Verkäufe über den Vergleich mit dem Vorgänger schätzen."""
import gzip
import json
import math
import time
from collections import defaultdict
from email.utils import parsedate_to_datetime

from .config import DATA_DIR

# Restlaufzeiten, bei denen ein Verschwinden zwischen zwei Scans sehr wahrscheinlich ein Verkauf ist
SOLD_TIME_LEFT = ("LONG", "VERY_LONG")


def aggregate(listings):
    """listings: Liste von (stückpreis_kupfer, menge). Liefert Angebots-Kennzahlen."""
    listings.sort()
    total = sum(q for _, q in listings)
    if total <= 0:
        return None
    # Marktpreis = Durchschnitt der günstigsten 15 % der Menge (robust gegen Einzelausreißer)
    need = max(1, math.ceil(total * 0.15))
    taken = 0
    cost = 0
    for price, qty in listings:
        take = min(qty, need - taken)
        cost += price * take
        taken += take
        if taken >= need:
            break
    market = round(cost / taken)
    half = total / 2
    acc = 0
    median = listings[-1][0]
    for price, qty in listings:
        acc += qty
        if acc >= half:
            median = price
            break
    return total, len(listings), listings[0][0], market, median


def parse_auctions(auctions):
    current = {}
    per_item = defaultdict(list)
    for a in auctions:
        try:
            item_id = int(a["item"]["id"])
        except (KeyError, TypeError, ValueError):
            continue
        qty = int(a.get("quantity", 1) or 1)
        unit = a.get("unit_price")
        if unit is None:
            buyout = a.get("buyout")
            if not buyout:
                continue  # reine Gebotsauktion ohne Sofortkauf
            unit = buyout / qty
        unit = int(round(unit))
        current[str(a["id"])] = [item_id, qty, a.get("time_left", ""), unit]
        per_item[item_id].append((unit, qty))
    return current, per_item


def estimate_sold(prev, current, current_min, gap_h=1.0):
    """Schätzt Verkäufe aus dem Vergleich zweier Snapshots.

    Gezählt wird nur, was mit hoher Wahrscheinlichkeit gekauft wurde:
    - Stapel, die kleiner geworden sind (Teilkauf bei Rohstoffen)
    - verschwundene Auktionen mit langer Restlaufzeit, die BILLIGER waren als alles,
      was jetzt noch im AH steht. Käufer nehmen immer das günstigste Angebot;
      abgebrochene/neu eingestellte Auktionen (Unterbieten) liegen dagegen typischerweise
      nicht unter dem neuen Mindestpreis und werden so nicht mehr als Verkauf gezählt.
    Bei größeren Lücken (> 2 h) zählen nur noch "VERY_LONG"-Auktionen (12–48 h Restlaufzeit),
    damit in der Lücke regulär abgelaufene Auktionen nicht als Verkauf gelten.
    """
    allowed = ("VERY_LONG",) if gap_h > 2 else SOLD_TIME_LEFT
    sold = defaultdict(float)
    for aid, entry in prev.items():
        item_id, qty, time_left = entry[0], entry[1], entry[2]
        price = entry[3] if len(entry) > 3 else None
        now = current.get(aid)
        if now is not None:
            if now[1] < qty:
                sold[item_id] += qty - now[1]
        elif time_left in allowed and price is not None:
            floor = current_min.get(item_id)
            if floor is None or price < floor:
                sold[item_id] += qty
    return sold


def process_snapshot(db, source, ts, auctions, cfg, keep=None):
    analysis = cfg["analysis"]
    current, per_item = parse_auctions(auctions)
    if keep:
        # Nur Items speichern, die in Rezepten vorkommen -> Datenbank bleibt klein
        current = {aid: v for aid, v in current.items() if v[0] in keep}
        per_item = {item: lst for item, lst in per_item.items() if item in keep}
    prev_path = DATA_DIR / f"last_{source}.json.gz"

    prev = None
    interval = None
    last = db.last_snapshot(source)
    if last and prev_path.exists():
        try:
            with gzip.open(prev_path, "rt", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, json.JSONDecodeError):
            stored = {}
        if stored.get("ts") == last[1]:
            gap = (ts - last[1]) / 3600
            if 0 < gap <= float(analysis["max_gap_hours"]):
                prev = stored["auctions"]
                interval = gap

    current_min = {item: min(p for p, _ in lst) for item, lst in per_item.items() if lst}
    sold = estimate_sold(prev, current, current_min, interval or 1.0) if prev is not None else {}

    rows = []
    for item_id in set(per_item) | set(sold):
        agg = aggregate(per_item[item_id]) if item_id in per_item else None
        total, n, mn, mk, md = agg if agg else (0, 0, None, None, None)
        rows.append((item_id, total, n, mn, mk, md, sold.get(item_id, 0.0) if prev is not None else None))

    db.insert_snapshot(source, ts, interval, rows, int(analysis["retention_days"]))

    tmp = prev_path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump({"ts": ts, "auctions": current}, f)
    tmp.replace(prev_path)

    return {
        "auctions": len(current),
        "items": len(per_item),
        "interval_h": interval,
        "sold_units": round(sum(sold.values())),
    }


def _snapshot_ts(response):
    header = response.headers.get("Last-Modified")
    if header:
        try:
            return int(parsedate_to_datetime(header).timestamp())
        except (TypeError, ValueError):
            pass
    return int(time.time())


def _relevant_items():
    from .recipes import load_recipes
    recipes = load_recipes()
    if not recipes:
        return None
    keep = set()
    for r in recipes:
        keep.add(r["crafted_id"])
        keep.update(rg["id"] for rg in r["reagents"] if rg.get("id"))
    return keep


def record_token(api, db):
    """Preis der WoW-Marke mitschreiben. Fehler hier dürfen den Scan nie stoppen."""
    try:
        data = api.wow_token()
    except Exception as exc:  # noqa: BLE001
        print(f"Hinweis: WoW-Marken-Preis nicht abrufbar ({exc}).")
        return None
    if not data or "price" not in data:
        return None
    ts = int(data.get("last_updated_timestamp", time.time() * 1000) / 1000)
    db.put_token(ts, int(data["price"]))
    print(f"WoW-Marke: {int(data['price']) // 10000:,} Gold".replace(",", "."))
    return int(data["price"])


def run_scan(api, cfg, db):
    record_token(api, db)
    keep = _relevant_items() if cfg["analysis"].get("only_recipe_items") else None
    sources = [("commodity", "Rohstoffe (regionsweit)", api.commodities)]
    realm_slug = cfg["realm"].get("slug")
    if realm_slug:
        crid = api.connected_realm_id(realm_slug)
        sources.append(("realm", f"Realm-Auktionen ({realm_slug})", lambda: api.realm_auctions(crid)))
    else:
        print("Hinweis: Kein Realm in config.toml – nur Rohstoffe werden gescannt (keine Ausrüstung/Deko).")

    results = {}
    for source, label, fetch in sources:
        print(f"Lade {label} ...", flush=True)
        response = fetch()
        if response is None:
            print(f"  Keine Daten erhalten für {label}.")
            continue
        ts = _snapshot_ts(response)
        if db.has_snapshot(source, ts):
            print("  Blizzard hat seit dem letzten Scan noch keine neuen Daten (Update etwa stündlich).")
            continue
        auctions = response.json().get("auctions", [])
        info = process_snapshot(db, source, ts, auctions, cfg, keep)
        results[source] = info
        if info["interval_h"] is None:
            demand = "Nachfrage: ab dem nächsten Scan (Vergleich nötig)"
        else:
            demand = f"~{info['sold_units']:,} Einheiten verkauft in {info['interval_h']:.1f} h".replace(",", ".")
        print(f"  {info['auctions']:,} Auktionen, {info['items']:,} Items | {demand}".replace(",", "."))
    return results
