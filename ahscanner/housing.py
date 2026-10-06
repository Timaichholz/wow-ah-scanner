"""Housing-Deko: Welche Deko-Rezepte lohnen sich, was brauchen sie, wie gut verkaufen sie sich?"""
from .analysis import _reagent_price
from .farm import expansion_of_tier
from .items import ensure_items

# Deine Berufe – Rezepte dieser Berufe werden als "für dich" markiert
MY_PROFESSIONS = ("schneiderei", "verzauberkunst")

CATEGORY_KEYWORDS = ("dekor", "deko ", "deko", "einricht", "wohn", "möbel", "behausung", "housing", "decor")
CLASS_KEYWORDS = ("dekor", "wohn", "behausung", "housing", "decor", "einricht")
HOUSING_CLASS_IDS = {20}
LUMBER_MIN_ID = 236000  # Holz ist ein neues Material (neue Item-IDs)

# realistische Obergrenze: so viele Stück verkauft ein einzelner Spieler höchstens pro Tag
MAX_SALES_PER_DAY = 10


def _has(text, keywords):
    t = (text or "").lower()
    return any(k in t for k in keywords)


def _is_lumber(rg):
    return "holz" in (rg.get("name") or "").lower() and (rg.get("id") or 0) >= LUMBER_MIN_ID


def find_decor_recipes(api, db, recipes):
    """Erkennt Deko-Rezepte über drei Signale: Kategorie, Itemklasse des Produkts, Holz als Material."""
    by_cat, by_lumber, rest = [], [], []
    for r in recipes:
        if _has(r.get("category"), CATEGORY_KEYWORDS):
            by_cat.append(r)
        elif any(_is_lumber(rg) for rg in r.get("reagents") or []):
            by_lumber.append(r)
        else:
            rest.append(r)
    meta = ensure_items(api, db, [r["crafted_id"] for r in by_cat + by_lumber + rest], max_new=1500)
    by_class = []
    for r in rest:
        m = meta.get(r["crafted_id"]) or {}
        if m.get("class_id") in HOUSING_CLASS_IDS or _has(m.get("class_name"), CLASS_KEYWORDS):
            by_class.append(r)
    stats = {"kategorie": len(by_cat), "holz": len(by_lumber), "itemklasse": len(by_class)}
    # Diagnose: welche Itemklassen und Kategorien gibt es überhaupt? (hilft, die Erkennung zu justieren)
    classes = {}
    for r in recipes:
        m = meta.get(r["crafted_id"]) or {}
        key = f"{m.get('class_id')}:{m.get('class_name')}"
        classes[key] = classes.get(key, 0) + 1
    stats["klassen"] = dict(sorted(classes.items(), key=lambda kv: -kv[1])[:20])
    stats["kategorien_schneiderei"] = sorted({r.get("category") for r in recipes
                                              if "schneiderei" in (r.get("profession") or "").lower()})[:60]
    stats["rezepte"] = len(recipes)
    if not (by_cat or by_lumber or by_class) and api is not None:
        stats["api"] = _api_probe(api)
    return by_cat + by_lumber + by_class, meta, stats


def _api_probe(api):
    """Diagnose: Kategorien direkt aus der Blizzard-API (Schneiderei) – stehen dort Deko-Rezepte?"""
    out = {}
    try:
        for prof in (api.profession_index() or {}).get("professions", []):
            if "schneiderei" not in (prof.get("name") or "").lower():
                continue
            detail = api.profession(prof["id"]) or {}
            for tier in (detail.get("skill_tiers") or [])[:3]:
                tdata = api.skill_tier(prof["id"], tier["id"]) or {}
                cats = {c.get("name"): len(c.get("recipes") or []) for c in tdata.get("categories") or []}
                out[tier.get("name")] = cats
                deco = [c for c in tdata.get("categories") or [] if _has(c.get("name"), CATEGORY_KEYWORDS)]
                if deco and deco[0].get("recipes"):
                    rec = api.recipe(deco[0]["recipes"][0]["id"]) or {}
                    out["beispielrezept"] = {k: rec.get(k) for k in ("name", "crafted_item", "reagents")}
            out["stufen"] = [t.get("name") for t in detail.get("skill_tiers") or []]
    except Exception as exc:  # noqa: BLE001
        out["fehler"] = str(exc)
    return out


def analyze_housing(api, db, mv, cfg, recipes):
    a = cfg["analysis"]
    cut = float(a.get("ah_cut", 0.05))
    decor, meta, stats = find_decor_recipes(api, db, recipes)

    rows = {}
    for r in decor:
        out_id = r["crafted_id"]
        st = mv.stats(out_id)
        sold = mv.sold_per_day(out_id)
        if st and st.get("min_price"):
            sell, supply, n_auctions = st["min_price"], st["total_qty"], st["n_auctions"]
        else:
            sell, supply, n_auctions = mv.last_price(out_id), 0, 0
        avg = mv.avg_price(out_id)
        if sell and avg and avg < sell:
            sell = avg  # konservativ: nie über dem Durchschnittspreis
        flags = []
        reagents, cost, missing = [], 0, 0
        for rg in r.get("reagents") or []:
            if not rg.get("qty") or not rg.get("id"):
                continue
            price, origin = _reagent_price(mv, cfg, rg["id"])
            line = price * rg["qty"] if price else 0
            cost += line
            if not price:
                missing += 1
            reagents.append({"id": rg["id"], "name": rg.get("name") or f"Item {rg['id']}", "qty": rg["qty"],
                             "unit_price": price, "origin": origin, "line_cost": line, "lumber": _is_lumber(rg)})
        if r.get("slot_names"):
            flags.append("Zusatz-Slots: " + ", ".join(r["slot_names"][:3]))
        if missing:
            flags.append(f"{missing} Material(ien) nicht im AH – selbst sammeln")
        if not sell:
            flags.append("noch nie im AH gesehen – Preis unbekannt")
        elif n_auctions == 0:
            flags.append("aktuell kein Angebot")
        if sold is not None and sold < 0.5:
            flags.append("verkauft sich kaum")
        profit = sell * r["crafted_qty"] * (1 - cut) - cost if sell else None
        per_item = profit / r["crafted_qty"] if profit is not None else None
        potential = None
        if per_item is not None and sold is not None:
            potential = per_item * min(sold * float(a.get("market_share", 0.2)), MAX_SALES_PER_DAY)
        m = meta.get(out_id) or {}
        row = {
            "recipe_id": r["id"], "item_id": out_id, "item": r.get("crafted_name") or m.get("name") or r["name"],
            "profession": r["profession"], "tier": r["tier"], "expansion": expansion_of_tier(r["tier"]) or r["tier"],
            "category": r.get("category"), "mine": _has(r["profession"], MY_PROFESSIONS),
            "sell_price": sell, "cost": cost, "profit": profit,
            "margin": (profit / cost) if (profit is not None and cost) else None,
            "sold_per_day": sold, "supply": supply, "n_auctions": n_auctions,
            "days_supply": (supply / sold) if sold else None,
            "potential_per_day": potential, "reagents": reagents, "flags": flags,
            "cost_complete": missing == 0,
        }
        prev = rows.get(out_id)
        if prev is None or (row["profit"] or -1e18) > (prev["profit"] or -1e18):
            rows[out_id] = row

    out = list(rows.values())
    out.sort(key=lambda x: (x["potential_per_day"] is not None, x["potential_per_day"] or 0,
                            x["profit"] is not None, x["profit"] or 0), reverse=True)

    # Zusammenfassung pro Beruf
    profs = {}
    for x in out:
        p = profs.setdefault(x["profession"], {"profession": x["profession"], "mine": x["mine"], "recipes": 0,
                                               "profitable": 0, "selling": 0, "best": None})
        p["recipes"] += 1
        if (x["profit"] or 0) > 0:
            p["profitable"] += 1
        if (x["sold_per_day"] or 0) >= 1:
            p["selling"] += 1
        if (x["potential_per_day"] or 0) > ((p["best"] or {}).get("potential_per_day") or 0):
            p["best"] = x
    prof_list = sorted(profs.values(), key=lambda p: (not p["mine"], -((p["best"] or {}).get("potential_per_day") or 0)))

    # Materialbedarf deiner Berufe: was taucht in den lohnenden Deko-Rezepten am häufigsten auf?
    mats = {}
    for x in out:
        if not x["mine"] or (x["profit"] or 0) <= 0:
            continue
        for rg in x["reagents"]:
            mm = mats.setdefault(rg["id"], {"id": rg["id"], "name": rg["name"], "recipes": 0, "unit_price": rg["unit_price"],
                                            "origin": rg["origin"], "lumber": rg["lumber"]})
            mm["recipes"] += 1
    mat_list = sorted(mats.values(), key=lambda m: (-m["recipes"], m["name"]))[:40]
    return {"rows": out, "professions": prof_list, "materials": mat_list, "detect": stats}
