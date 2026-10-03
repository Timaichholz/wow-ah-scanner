"""Farm-Analyse: Farmspots, Rohstoffe nach Tätigkeit, Transmog-Markt, WoW-Marke und Datenstatus."""
import json
import math
import time

from .config import BASE_DIR
from .items import TRADE_GOODS_CLASS, activity_for, ensure_items, resolve_names

FARMSPOTS_FILE = BASE_DIR / "farmspots.json"

# Erweiterung aus dem Namen der Berufsstufe ableiten (deutsche Blizzard-Namen)
EXPANSIONS = [
    ("midnight", "Midnight"), ("quel", "Midnight"),
    ("khaz algar", "The War Within"),
    ("dracheninseln", "Dragonflight"), ("drachen", "Dragonflight"),
    ("schattenlande", "Shadowlands"),
    ("kul tiras", "Battle for Azeroth"), ("zandalar", "Battle for Azeroth"),
    ("verheerten inseln", "Legion"),
    ("draenor", "Warlords of Draenor"),
    ("pandaria", "Mists of Pandaria"),
    ("kataklysmus", "Cataclysm"),
    ("nordend", "Wrath of the Lich King"),
    ("scherbenwelt", "Burning Crusade"),
    ("klassisch", "Classic"),
]
CONF_WEIGHT = {"hoch": 1.0, "mittel": 0.8, "niedrig": 0.5}


def expansion_of_tier(tier):
    t = (tier or "").lower()
    for key, label in EXPANSIONS:
        if key in t:
            return label
    return None


def item_expansions(recipes):
    """Item-ID -> häufigste Erweiterung, in deren Rezepten das Item vorkommt."""
    counts = {}
    for r in recipes:
        exp = expansion_of_tier(r.get("tier"))
        if not exp:
            continue
        ids = [r.get("crafted_id")] + [rg.get("id") for rg in r.get("reagents", [])]
        for i in ids:
            if i:
                counts.setdefault(i, {}).setdefault(exp, 0)
                counts[i][exp] += 1
    return {i: max(c, key=c.get) for i, c in counts.items()}


def load_farmspots():
    if not FARMSPOTS_FILE.exists():
        return []
    with open(FARMSPOTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _price(st):
    if not st:
        return None
    return st.get("min_price") or st.get("market_price")


# ---------------------------------------------------------------------------
# Farmspots
# ---------------------------------------------------------------------------
def analyze_farmspots(api, db, mv, cfg):
    spots = load_farmspots()
    if not spots:
        return []
    all_names = sorted({n for s in spots for n in s.get("loot_items", [])})
    name_ids = resolve_names(api, db, all_names)
    all_ids = {i for ids in name_ids.values() for i in ids}
    meta = ensure_items(api, db, all_ids)

    logs = {}
    for entry in cfg.get("farm_log", []) or []:
        sid = entry.get("spot")
        if sid and entry.get("minutes"):
            agg = logs.setdefault(sid, {"minutes": 0, "gold": 0, "sessions": 0})
            agg["minutes"] += float(entry["minutes"])
            agg["gold"] += float(entry.get("gold", 0)) * 10000  # Eingabe in Gold, intern Kupfer
            agg["sessions"] += 1

    result = []
    for s in spots:
        loot = []
        for name in s.get("loot_items", []):
            ids = name_ids.get(name, [])
            # bei mehreren IDs (Qualitätsstufen) die mit dem meisten Umsatz/Angebot nehmen
            best = None
            for i in ids or [None]:
                st = mv.stats(i) if i else None
                sold = mv.sold_per_day(i) if i else None
                cand = {"item_id": i, "stats": st, "sold": sold}
                key = ((sold or 0) * (_price(st) or 0), (st or {}).get("total_qty", 0))
                if best is None or key > best["_key"]:
                    cand["_key"] = key
                    best = cand
            i = best["item_id"]
            st = best["stats"]
            m = meta.get(i, {}) if i else {}
            loot.append({
                "name_en": name,
                "name": m.get("name") or name,
                "item_id": i,
                "found": bool(ids),
                "price": _price(st),
                "avg": mv.avg_price(i) if i else None,
                "supply": (st or {}).get("total_qty", 0),
                "n_auctions": (st or {}).get("n_auctions", 0),
                "sold_per_day": best["sold"],
                "drop_rate": (s.get("drop_rates") or {}).get(name, ""),
                "binding": m.get("binding"),
            })

        priced = [l for l in loot if l["price"]]
        volume = sum((l["price"] or 0) * (l["sold_per_day"] or 0) for l in loot)
        has_demand = any(l["sold_per_day"] is not None for l in loot)
        sells = [l for l in loot if (l["sold_per_day"] or 0) > 0]
        top_value = max((l["price"] for l in priced), default=0)
        weight = CONF_WEIGHT.get(s.get("confidence"), 0.6)
        if has_demand:
            score = volume * weight
        else:
            score = top_value * 0.01 * weight  # nur Preis bekannt -> deutlich vorsichtiger gewichtet

        if not priced:
            verdict = "Beute aktuell nicht im AH"
        elif has_demand and sells:
            verdict = "Beute verkauft sich"
        elif has_demand:
            verdict = "Kein Absatz gemessen"
        else:
            verdict = "Noch keine Nachfragedaten"

        log = logs.get(s["id"])
        gph = (log["gold"] / log["minutes"] * 60) if log and log["minutes"] else None
        result.append({**s, "loot": loot, "market_volume_day": volume, "top_value": top_value,
                       "n_selling": len(sells), "score": score, "verdict": verdict,
                       "measured_gph": gph, "log": log})
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Rohstoffe nach Tätigkeit
# ---------------------------------------------------------------------------
def analyze_raw_materials(api, db, mv, cfg, recipes, per_group=None, candidates=2500):
    per_group = per_group or int(cfg["analysis"].get("raw_per_group", 30))
    exp_map = item_expansions(recipes or [])
    excluded = {k.lower() for k in cfg["filter"].get("tier_exclude_keywords") or []}
    exclude_midnight = any(k in ("midnight", "quel") for k in excluded)

    rows = []
    for item_id, st in mv.latest.items():
        if st["source"] != "commodity" or not st.get("market_price"):
            continue
        sold = mv.sold_per_day(item_id)
        price = st["market_price"]
        weight = (sold if sold is not None else min(st["total_qty"], 2000) / 50)
        rows.append((price * weight, item_id, st, sold))
    rows.sort(reverse=True)
    top = rows[:candidates]
    meta = ensure_items(api, db, [r[1] for r in top])

    my_acts = {a.lower() for a in cfg.get("player", {}).get("activities", [])}
    groups = {}
    for _, item_id, st, sold in top:
        m = meta.get(item_id)
        act = activity_for(m)
        if not act:
            continue
        exp = exp_map.get(item_id)
        if exclude_midnight and exp == "Midnight":
            continue
        avg = mv.avg_price(item_id)
        price = st["market_price"]
        groups.setdefault(act[0], {"label": act[0], "how": act[1], "mine": act[0].lower() in my_acts, "items": []})
        groups[act[0]]["items"].append({
            "item_id": item_id, "name": m.get("name") or f"Item {item_id}", "expansion": exp or "–",
            "price": price, "supply": st["total_qty"], "n_auctions": st["n_auctions"],
            "sold_per_day": sold, "gold_volume": price * sold if sold is not None else None,
            "days_supply": st["total_qty"] / sold if sold else None,
            "trend": (price / avg - 1) if avg else None,
        })
    has_demand = any(it["gold_volume"] is not None for g in groups.values() for it in g["items"])
    for g in groups.values():
        key = (lambda it: it["gold_volume"] or 0) if has_demand else (lambda it: it["price"])
        g["items"].sort(key=key, reverse=True)
        g["items"] = g["items"][:per_group]
        g["volume"] = sum(it["gold_volume"] or 0 for it in g["items"])
    ordered = sorted(groups.values(), key=lambda g: (g["mine"], g["volume"]), reverse=True)
    return ordered


# ---------------------------------------------------------------------------
# Transmog-/BoE-Markt (Realm)
# ---------------------------------------------------------------------------
def analyze_transmog(api, db, mv, cfg, recipes, limit=None, candidates=600, min_price_gold=500):
    limit = limit or int(cfg["analysis"].get("transmog_limit", 150))
    crafted = {r.get("crafted_id") for r in recipes or []}
    rows = []
    for item_id, st in mv.latest.items():
        if st["source"] != "realm" or item_id in crafted:
            continue
        price = st.get("min_price") or 0
        if price < min_price_gold * 10000:
            continue
        sold = mv.sold_per_day(item_id)
        rows.append((price * (sold if sold else 0.05), item_id, st, sold))
    rows.sort(reverse=True)
    top = rows[:candidates]
    meta = ensure_items(api, db, [r[1] for r in top])
    out = []
    demand_ready = mv.demand_hours() >= float(cfg["analysis"].get("min_demand_hours", 12))
    for _, item_id, st, sold in top:
        m = meta.get(item_id) or {}
        if m and not m.get("equippable"):
            continue  # nur tragbare Ausrüstung (Transmog)
        if demand_ready and not sold:
            continue
        spread = (st["median_price"] / st["min_price"]) if st.get("min_price") and st.get("median_price") else None
        out.append({
            "item_id": item_id, "name": m.get("name") or f"Item {item_id}",
            "quality": m.get("quality"), "required_level": m.get("required_level"),
            "binding": m.get("binding"), "price": st["min_price"], "supply": st["total_qty"],
            "n_auctions": st["n_auctions"], "sold_per_day": sold,
            "days_supply": st["total_qty"] / sold if sold else None,
            "spread": spread,
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Volumen: viel Umsatz, auch bei kleinem Stückpreis
# ---------------------------------------------------------------------------
def analyze_volume(api, db, mv, cfg, recipes, limit=100):
    """Rohstoffe/Handelswaren mit dem höchsten Gold-Umsatz pro Tag – egal wie klein der Stückpreis ist."""
    exp_map = item_expansions(recipes or [])
    rows = []
    for item_id, st in mv.latest.items():
        if st["source"] != "commodity" or not st.get("market_price"):
            continue
        sold = mv.sold_per_day(item_id)
        if not sold:
            continue
        rows.append((st["market_price"] * sold, item_id, st, sold))
    rows.sort(reverse=True)
    top = rows[: limit * 2]
    meta = ensure_items(api, db, [r[1] for r in top])
    out = []
    for vol, item_id, st, sold in top:
        if exp_map.get(item_id) == "Midnight":
            continue
        m = meta.get(item_id) or {}
        act = activity_for(m)
        out.append({"item_id": item_id, "name": m.get("name") or f"Item {item_id}",
                    "kind": act[0] if act else (m.get("class_name") or "–"),
                    "how": act[1] if act else "", "expansion": exp_map.get(item_id, "–"),
                    "price": st["market_price"], "sold_per_day": sold, "gold_volume": vol,
                    "supply": st["total_qty"], "days_supply": st["total_qty"] / sold if sold else None})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Schnäppchen / Flipping: unter Durchschnitt kaufen, zum Durchschnitt wieder verkaufen
# ---------------------------------------------------------------------------
def analyze_flips(api, db, mv, cfg, recipes, limit=80):
    a = cfg["analysis"]
    cut = float(a.get("ah_cut", 0.05))
    share = float(a.get("market_share", 0.2))
    min_disc = float(a.get("flip_min_discount", 0.15))
    min_sold = float(a.get("flip_min_sold_per_day", 3))
    exp_map = item_expansions(recipes or [])
    rows = []
    for item_id, st in mv.latest.items():
        avg = mv.avg_price(item_id)
        sold = mv.sold_per_day(item_id)
        if not avg or not sold or sold < (min_sold if st["source"] == "commodity" else 0.3):
            continue
        buy = st.get("market_price") if st["source"] == "commodity" else st.get("min_price")
        if not buy or buy > avg * (1 - min_disc):
            continue
        margin = avg * (1 - cut) - buy
        if margin <= 0:
            continue
        potential = margin * sold * share
        rows.append((potential, item_id, st, sold, avg, buy, margin))
    rows.sort(reverse=True)
    top = rows[: limit * 2]
    meta = ensure_items(api, db, [r[1] for r in top])
    out = []
    for potential, item_id, st, sold, avg, buy, margin in top:
        if exp_map.get(item_id) == "Midnight":
            continue
        m = meta.get(item_id) or {}
        out.append({"item_id": item_id, "name": m.get("name") or f"Item {item_id}",
                    "market": "Rohstoff (EU)" if st["source"] == "commodity" else "Realm",
                    "buy": buy, "avg": avg, "discount": 1 - buy / avg, "margin": margin,
                    "sold_per_day": sold, "supply": st["total_qty"], "potential_per_day": potential})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# WoW-Marke & Midnight-Ziel
# ---------------------------------------------------------------------------
def analyze_token(db, cfg):
    goal = cfg.get("goal", {})
    hist = db.token_history(int(time.time() - 7 * 86400))
    if not hist:
        return None
    prices = [p for _, p in hist]
    now = prices[-1]
    tokens = math.ceil(float(goal.get("price_eur", 49.99)) / float(goal.get("balance_per_token_eur", 12.99)) - 1e-9)
    need = now * tokens
    have = int(float(goal.get("current_gold", 0)) * 10000)
    avg = sum(prices) / len(prices)
    return {
        "price": now, "min7": min(prices), "max7": max(prices), "avg7": avg,
        "vs_avg": now / avg - 1 if avg else 0, "tokens": tokens, "need": need,
        "have": have, "missing": max(0, need - have), "label": goal.get("label", "Midnight"),
        "points": len(prices), "updated": hist[-1][0],
    }


# ---------------------------------------------------------------------------
# Datenstatus
# ---------------------------------------------------------------------------
def data_health(db, mv):
    cutoff = int(time.time() - 48 * 3600)
    out = {"demand_hours": mv.demand_hours(), "sources": {}}
    for src in ("commodity", "realm"):
        rows = db.snapshot_times(src, cutoff)
        if not rows:
            continue
        ts = [r[0] for r in rows]
        gaps = [(b - a) / 3600 for a, b in zip(ts, ts[1:])]
        out["sources"][src] = {
            "scans_48h": len(ts), "last": ts[-1], "max_gap": max(gaps) if gaps else 0,
            "big_gaps": sum(1 for g in gaps if g > 4),
            "age_h": (time.time() - ts[-1]) / 3600,
        }
    return out
