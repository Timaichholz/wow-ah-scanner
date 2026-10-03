"""Auswertung: Herstellkosten vs. Verkaufspreis, Angebot und Nachfrage pro Rezept und Material."""
import time


def money(copper):
    if copper is None:
        return "–"
    sign = "-" if copper < 0 else ""
    c = abs(int(round(copper)))
    gold, rest = divmod(c, 10000)
    silver, cu = divmod(rest, 100)
    if gold >= 100:
        return f"{sign}{gold:,}g".replace(",", ".")
    if gold > 0:
        return f"{sign}{gold}g {silver}s"
    return f"{sign}{silver}s {cu}c"


def _matches(text, keywords):
    if not keywords:
        return True
    text = (text or "").lower()
    return any(k.lower() in text for k in keywords)


class MarketView:
    def __init__(self, db, cfg):
        days = int(cfg["analysis"]["history_days"])
        self.min_hours = float(cfg["analysis"].get("demand_min_hours", 3))
        self.latest = db.latest_stats()
        self.hist = db.history(int(time.time() - days * 86400))
        self.hours = self.hist["hours"]

    def stats(self, item_id):
        return self.latest.get(item_id)

    def _source(self, item_id):
        st = self.latest.get(item_id)
        if st:
            return st["source"]
        for src in ("commodity", "realm"):
            if (src, item_id) in self.hist["sold"]:
                return src
        return None

    def sold_per_day(self, item_id):
        src = self._source(item_id)
        if not src:
            return None
        hours = self.hours.get(src, 0)
        if hours < self.min_hours:  # zu wenig Verlauf für eine seriöse Hochrechnung
            return None
        return self.hist["sold"].get((src, item_id), 0.0) / hours * 24

    def avg_price(self, item_id):
        src = self._source(item_id)
        return self.hist["avg"].get((src, item_id)) if src else None

    def last_price(self, item_id):
        return self.hist["last_price"].get(item_id)

    def demand_hours(self):
        return max(self.hours.values()) if self.hours else 0.0


def _reagent_price(mv, cfg, item_id):
    vendor = cfg["vendor_prices"].get(str(item_id))
    if vendor is not None:
        return vendor, "Händler"
    st = mv.stats(item_id)
    if st and st["market_price"]:
        return st["market_price"], "AH"
    last = mv.last_price(item_id)
    if last:
        return last, "AH (alt)"
    return None, "farmen"


def classify(invest, needs_farming, sold, a, cost=None):
    """Ordnet eine Gelegenheit nach Aufwand ein.

    Easy Money    – alle Materialien im AH kaufbar, wenig Startkapital, verkauft sich regelmäßig
    Kapitalintensiv – hoher Goldeinsatz pro Charge
    Zeitintensiv  – Materialien müssen selbst gefarmt werden
    Solide        – alles dazwischen
    """
    easy_invest = float(a.get("easy_max_invest_gold", 5000)) * 10000
    easy_sold = float(a.get("easy_min_sold_per_day", 2))
    capital = float(a.get("capital_threshold_gold", 50000)) * 10000
    high_capital = invest > capital
    if needs_farming and high_capital:
        return "Zeit + Kapital"
    if needs_farming:
        return "Zeitintensiv"
    if high_capital:
        return "Kapitalintensiv"
    if not cost:
        return "Solide"  # Kosten unbekannt -> nie als Easy Money einstufen
    if invest <= easy_invest and (sold is None or sold >= easy_sold):
        return "Easy Money" if sold is not None else "Easy Money?"
    return "Solide"


def filter_recipes(recipes, cfg, known_ids=None):
    f = cfg["filter"]
    out = []
    for r in recipes:
        if not _matches(r["profession"], f["professions"]):
            continue
        if not _matches(r["tier"], f["tier_keywords"]):
            continue
        excl = f.get("tier_exclude_keywords") or []
        if excl and any(k.lower() in (r["tier"] or "").lower() for k in excl):
            continue
        if not _matches(r["category"], f["category_keywords"]):
            continue
        if known_ids is not None and r["id"] not in known_ids:
            continue
        out.append(r)
    return out


def analyze_recipes(recipes, mv, cfg):
    a = cfg["analysis"]
    cut = float(a["ah_cut"])
    share = float(a["market_share"])
    min_profit = float(a["min_profit_gold"]) * 10000
    competition = int(a["competition_threshold"])

    min_auctions = int(a.get("min_auctions", 3))
    min_sold = float(a.get("min_sold_per_day", 0.5))
    demand_ready = mv.demand_hours() >= float(a.get("min_demand_hours", 12))
    skipped = {"thin": 0, "no_sales": 0}

    best = {}
    for r in recipes:
        if not r.get("reagents"):
            continue  # nur Qualitäts-Slots ohne Item-IDs -> Kosten nicht berechenbar
        out_id = r["crafted_id"]
        st = mv.stats(out_id)
        flags = []
        sold = mv.sold_per_day(out_id)
        sells_well = sold is not None and sold >= min_sold

        if st and st["min_price"]:
            sell = st["min_price"]
            supply, n_auctions = st["total_qty"], st["n_auctions"]
        else:
            sell = mv.last_price(out_id)
            if not sell:
                continue  # nie im AH gesehen -> kein Preis bekannt
            supply, n_auctions = 0, 0
            flags.append("aktuell kein Angebot")

        # Plausibilität: Einzelangebote mit Fantasiepreisen nur zeigen, wenn nachweislich verkauft wird
        if n_auctions < min_auctions and not sells_well:
            skipped["thin"] += 1
            continue
        # Mit genug Verlauf: Dinge, die sich nicht verkaufen, fliegen raus
        if demand_ready and not sells_well:
            skipped["no_sales"] += 1
            continue
        # Konservativ: nie über dem Durchschnittspreis des Zeitraums kalkulieren
        avg_out = mv.avg_price(out_id)
        if avg_out and avg_out < sell:
            sell = avg_out

        cost = 0
        reagents = []
        for rg in r["reagents"]:
            if not rg.get("qty") or not rg.get("id"):
                continue  # leere Reagenz-Einträge aus der API überspringen
            rg = {**rg, "name": rg.get("name") or f"Item {rg['id']}"}
            price, origin = _reagent_price(mv, cfg, rg["id"])
            line_cost = price * rg["qty"] if price else 0
            cost += line_cost
            reagents.append({**rg, "unit_price": price, "origin": origin, "line_cost": line_cost})
        if any(rg["origin"] == "farmen" for rg in reagents):
            flags.append("Material nicht im AH – selbst farmen")
        if cost == 0:
            flags.append("Kosten unbekannt – Gewinn überschätzt")
        if r["has_modified_slots"]:
            flags.append("Qualitäts-/Optional-Slots – Kosten evtl. unvollständig")
        if st and st.get("source") == "realm" and st.get("min_price") and st.get("median_price") \
                and st["median_price"] > 3 * st["min_price"]:
            flags.append("Preise stark gestreut (evtl. verschiedene Gegenstandsstufen)")

        revenue = sell * r["crafted_qty"] * (1 - cut)
        profit = revenue - cost
        if profit < min_profit:
            continue

        days_supply = supply / sold if sold else None
        potential = profit / r["crafted_qty"] * sold * share if sold is not None else None
        avg = mv.avg_price(out_id)
        trend = (sell / avg - 1) if avg else None

        if sold is None:
            rating = "Keine Nachfragedaten"
        elif sold < 0.5:
            rating = "Kaum Nachfrage"
            flags.append("verkauft sich kaum")
        elif days_supply is not None and days_supply > 7:
            rating = "Überangebot"
        elif n_auctions >= competition:
            rating = "Viel Konkurrenz"
        elif days_supply is not None and days_supply < 1.5:
            rating = "Top – knappes Angebot"
        else:
            rating = "Gut"
        if n_auctions >= competition:
            flags.append(f"{n_auctions} Angebote im AH")
        if days_supply is not None and days_supply > 7:
            flags.append(f"Angebot reicht {days_supply:.0f} Tage")

        batch = int(a["crafts_per_recipe"])
        invest = cost * batch
        needs_farming = any(rg["origin"] == "farmen" for rg in reagents)
        typ = classify(invest, needs_farming, sold, a, cost)

        row = {
            "type": typ,
            "invest": invest,
            "needs_farming": needs_farming,
            "recipe_id": r["id"],
            "recipe": r["name"],
            "item_id": out_id,
            "item": r["crafted_name"] or r["name"],
            "profession": r["profession"],
            "tier": r["tier"],
            "category": r["category"],
            "crafted_qty": r["crafted_qty"],
            "sell_price": sell,
            "cost": cost,
            "profit": profit,
            "margin": profit / cost if cost else None,
            "supply": supply,
            "n_auctions": n_auctions,
            "sold_per_day": sold,
            "days_supply": days_supply,
            "potential_per_day": potential,
            "trend": trend,
            "rating": rating,
            "flags": flags,
            "reagents": reagents,
        }
        prev = best.get(out_id)
        if prev is None or row["profit"] > prev["profit"]:
            best[out_id] = row

    rows = list(best.values())
    has_demand = any(r["potential_per_day"] is not None for r in rows)
    if has_demand:
        rows.sort(key=lambda r: (r["potential_per_day"] is not None, r["potential_per_day"] or 0, r["profit"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["profit"], reverse=True)
    return rows[: int(a["top_n"])], has_demand, skipped, rows


def analyze_materials(all_recipes, mv, cfg, limit=60):
    """Welche Rohstoffe bringen am meisten Gold pro Tag in Bewegung? -> Farm-Ziele."""
    names = {}
    for r in all_recipes:
        for rg in r["reagents"]:
            if rg.get("id") and rg.get("name"):
                names.setdefault(rg["id"], rg["name"])
    rows = []
    for item_id, name in names.items():
        st = mv.stats(item_id)
        if not st or not st["market_price"] or st.get("source") != "commodity":
            continue  # nur echte Rohstoffe (regionsweit gehandelt), keine Ausrüstung
        sold = mv.sold_per_day(item_id)
        avg = mv.avg_price(item_id)
        price = st["market_price"]
        rows.append({
            "item_id": item_id,
            "item": name,
            "price": price,
            "supply": st["total_qty"],
            "n_auctions": st["n_auctions"],
            "sold_per_day": sold,
            "gold_volume": price * sold if sold is not None else None,
            "days_supply": st["total_qty"] / sold if sold else None,
            "trend": (price / avg - 1) if avg else None,
        })
    if any(r["gold_volume"] is not None for r in rows):
        rows.sort(key=lambda r: r["gold_volume"] or 0, reverse=True)
    else:
        rows.sort(key=lambda r: r["price"], reverse=True)
    return rows[:limit]
