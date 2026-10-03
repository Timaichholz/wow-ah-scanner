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


def _norm(text):
    import re
    return re.sub(r"[^a-zäöüß]", "", (text or "").lower())


UNMAPPED_TIERS = set()


def expansion_of_tier(tier):
    t = _norm(tier)
    for key, label in EXPANSIONS:
        if _norm(key) in t:
            return label
    if tier:
        UNMAPPED_TIERS.add(tier)
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


class ExpResolver:
    """Ordnet Items einer Erweiterung zu – in drei Stufen:
    1. Item-ID kommt als Reagenz/Produkt in Rezepten vor (sicher)
    2. Itemname entspricht einem Qualitäts-Material-Slot in Rezepten (ab Dragonflight)
    3. Rückfall über Blizzards Item-ID-Bereiche (nur Dragonflight / The War Within, dort eindeutig)
    """

    def __init__(self, recipes):
        self.ids = item_expansions(recipes or [])
        names = {}
        # Alles, was in IRGENDEINEM Midnight-Rezept vorkommt, gilt als Midnight (sicher ausblenden)
        self.midnight_ids, self.midnight_names = set(), set()
        for r in recipes or []:
            exp = expansion_of_tier(r.get("tier"))
            if not exp:
                continue
            if exp == "Midnight":
                self.midnight_ids.update(rg.get("id") for rg in r.get("reagents") or [] if rg.get("id"))
                self.midnight_ids.add(r.get("crafted_id"))
                self.midnight_names.update(_norm(n) for n in r.get("slot_names") or [])
                self.midnight_names.update(_norm(rg.get("name")) for rg in r.get("reagents") or [] if rg.get("name"))
            for n in r.get("slot_names") or []:
                names.setdefault(_norm(n), {}).setdefault(exp, 0)
                names[_norm(n)][exp] += 1
        self.names = {k: max(v, key=v.get) for k, v in names.items()}

    def get(self, item_id, meta=None):
        if item_id in self.midnight_ids:
            return "Midnight"
        if meta and meta.get("name") and _norm(meta["name"]) in self.midnight_names:
            return "Midnight"
        exp = self.ids.get(item_id)
        if exp:
            return exp
        if meta and meta.get("name"):
            exp = self.names.get(_norm(meta["name"]))
            if exp:
                return exp
        if item_id and 190000 <= item_id < 210000:
            return "Dragonflight"
        if item_id and 210000 <= item_id < 236000:
            return "The War Within"
        if item_id and item_id >= 236000:
            return "Midnight"  # neuere Item-Nummern ohne Rezeptbezug -> sehr wahrscheinlich Midnight
        return None


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
    res = ExpResolver(recipes)
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
        exp = res.get(item_id, m)
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
    res = ExpResolver(recipes)
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
        m = meta.get(item_id) or {}
        exp = res.get(item_id, m)
        if exp == "Midnight":
            continue
        act = activity_for(m)
        out.append({"item_id": item_id, "name": m.get("name") or f"Item {item_id}",
                    "kind": act[0] if act else (m.get("class_name") or "–"),
                    "how": act[1] if act else "", "expansion": exp or "–",
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
    res = ExpResolver(recipes)
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
        m = meta.get(item_id) or {}
        if res.get(item_id, m) == "Midnight":
            continue
        out.append({"item_id": item_id, "name": m.get("name") or f"Item {item_id}",
                    "market": "Rohstoff (EU)" if st["source"] == "commodity" else "Realm",
                    "buy": buy, "avg": avg, "discount": 1 - buy / avg, "margin": margin,
                    "sold_per_day": sold, "supply": st["total_qty"], "potential_per_day": potential})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Matrix: Erweiterung × Beruf
# ---------------------------------------------------------------------------
EXP_ORDER = ["Classic", "Burning Crusade", "Wrath of the Lich King", "Cataclysm", "Mists of Pandaria",
             "Warlords of Draenor", "Legion", "Battle for Azeroth", "Shadowlands", "Dragonflight", "The War Within"]
# Sammel-Tätigkeit -> Unterklassen der Handwerkswaren
GATHER = [("Kräuterkunde", {9}), ("Bergbau", {7, 4}), ("Kürschnerei", {6}), ("Stoff", {5}),
          ("Entzaubern", {12}), ("Fleisch & Fisch", {8}), ("Elementar", {10})]


def analyze_matrix(api, db, mv, cfg, recipes, craft_rows, candidates=3000):
    res = ExpResolver(recipes)
    rows = []
    for item_id, st in mv.latest.items():
        if st["source"] != "commodity" or not st.get("market_price"):
            continue
        sold = mv.sold_per_day(item_id)
        rows.append((st["market_price"] * (sold or 0), item_id, st, sold))
    rows.sort(key=lambda r: (r[0], r[2]["market_price"]), reverse=True)
    rows = rows[:candidates]
    meta = ensure_items(api, db, [r[1] for r in rows])

    gather = {}
    for vol, item_id, st, sold in rows:
        m = meta.get(item_id) or {}
        if m.get("class_id") != TRADE_GOODS_CLASS:
            continue
        exp = res.get(item_id, m)
        if not exp or exp == "Midnight":
            continue
        for act, subs in GATHER:
            if m.get("subclass_id") in subs:
                c = gather.setdefault((exp, act), {"volume": 0.0, "items": [], "prices": []})
                c["volume"] += vol
                c["prices"].append(st["market_price"])
                c["items"].append({"item_id": item_id, "name": m.get("name") or f"Item {item_id}",
                                   "price": st["market_price"], "sold_per_day": sold,
                                   "gold_volume": vol if sold is not None else None})
    for c in gather.values():
        c["items"].sort(key=lambda it: (it["gold_volume"] or 0, it["price"]), reverse=True)
        c["items"] = c["items"][:5]
        ps = sorted(c.pop("prices"))
        c["median_price"] = ps[len(ps) // 2]
        c["n"] = len(ps)

    craft = {}
    for r in craft_rows or []:
        exp = expansion_of_tier(r.get("tier"))
        if not exp or exp == "Midnight":
            continue
        c = craft.setdefault((exp, r["profession"]), {"potential": 0.0, "n": 0, "items": [], "profits": []})
        c["potential"] += r.get("potential_per_day") or 0
        c["n"] += 1
        c["profits"].append(r["profit"])
        c["items"].append(r)
    for c in craft.values():
        c["items"].sort(key=lambda r: (r.get("potential_per_day") or 0, r["profit"]), reverse=True)
        c["items"] = [{"item_id": r.get("item_id"), "name": r["item"], "profit": r["profit"],
                       "sold_per_day": r.get("sold_per_day"), "potential": r.get("potential_per_day")}
                      for r in c["items"][:5]]
        ps = sorted(c.pop("profits"))
        c["median_profit"] = ps[len(ps) // 2]

    spots = load_farmspots()
    gather_cols = [a for a, _ in GATHER]
    craft_cols = sorted({k[1] for k in craft})
    used = {k[0] for k in list(gather) + list(craft)} | {s.get("expansion") for s in spots}
    per_exp = []
    for e in [x for x in EXP_ORDER if x in used]:
        g = {a: gather.get((e, a)) for a in gather_cols}
        cr = {p: craft.get((e, p)) for p in craft_cols}
        best_g = max(((a, c) for a, c in g.items() if c), key=lambda x: (x[1]["volume"], x[1]["median_price"]),
                     default=None)
        best_c = max(((p, c) for p, c in cr.items() if c), key=lambda x: (x[1]["potential"], x[1]["n"]),
                     default=None)
        per_exp.append({"expansion": e, "gather": g, "craft": cr, "best_gather": best_g, "best_craft": best_c,
                        "spots": [{"id": s["id"], "name_de": s["name_de"]} for s in spots if s.get("expansion") == e],
                        "volume": sum(c["volume"] for c in g.values() if c)})
    has_demand = any(it["gold_volume"] is not None for c in gather.values() for it in c["items"])
    return {"expansions": per_exp, "gather_cols": gather_cols, "craft_cols": craft_cols, "has_demand": has_demand}


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
