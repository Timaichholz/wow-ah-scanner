"""Rezept-Datenbank aus der Blizzard-API aufbauen (einmalig, danach aus dem Cache)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import RECIPES_FILE


def _crafted_quantity(rec):
    cq = rec.get("crafted_quantity") or {}
    if "value" in cq:
        return float(cq["value"]) or 1.0
    lo, hi = cq.get("minimum"), cq.get("maximum")
    if lo or hi:
        return ((lo or hi) + (hi or lo)) / 2
    return 1.0


def parse_recipe(rec, meta):
    crafted = rec.get("crafted_item") or rec.get("alliance_crafted_item") or rec.get("horde_crafted_item")
    if not crafted:
        return None  # z. B. Verzauberungen ohne Item
    reagents = []
    for r in rec.get("reagents", []) or []:
        reagent = r.get("reagent") or {}
        if not reagent.get("id") or not r.get("quantity"):
            continue
        reagents.append({"id": reagent["id"], "name": reagent.get("name") or f"Item {reagent['id']}", "qty": r["quantity"]})
    if not reagents:
        return None
    return {
        "id": rec["id"],
        "name": rec.get("name", ""),
        "profession": meta["profession"],
        "tier": meta["tier"],
        "category": meta["category"],
        "crafted_id": crafted["id"],
        "crafted_name": crafted.get("name", ""),
        "crafted_qty": _crafted_quantity(rec),
        "reagents": reagents,
        "has_modified_slots": bool(rec.get("modified_crafting_slots")),
    }


def build_cache(api, workers=8):
    started = time.time()
    index = api.profession_index() or {}
    todo = {}
    for prof in index.get("professions", []):
        detail = api.profession(prof["id"]) or {}
        tiers = detail.get("skill_tiers", []) or []
        if not tiers:
            continue
        print(f"  {prof['name']}: {len(tiers)} Erweiterungsstufen", flush=True)
        for tier in tiers:
            tdata = api.skill_tier(prof["id"], tier["id"]) or {}
            for cat in tdata.get("categories", []) or []:
                for rec in cat.get("recipes", []) or []:
                    todo[rec["id"]] = {
                        "profession": prof["name"],
                        "tier": tier.get("name", ""),
                        "category": cat.get("name", ""),
                    }

    print(f"Lade {len(todo):,} Rezeptdetails ...".replace(",", "."), flush=True)
    recipes = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(api.recipe, rid): rid for rid in todo}
        for fut in as_completed(futures):
            rid = futures[fut]
            done += 1
            try:
                data = fut.result()
            except Exception as exc:  # einzelne Fehler überspringen
                print(f"  Rezept {rid} übersprungen: {exc}")
                continue
            if data:
                parsed = parse_recipe(data, todo[rid])
                if parsed:
                    recipes.append(parsed)
            if done % 500 == 0:
                print(f"  {done:,}/{len(todo):,}".replace(",", "."), flush=True)

    with open(RECIPES_FILE, "w", encoding="utf-8") as f:
        json.dump({"built_at": int(time.time()), "recipes": recipes}, f, ensure_ascii=False)
    print(f"Fertig: {len(recipes):,} herstellbare Rezepte gespeichert ({time.time() - started:.0f} s).".replace(",", "."))
    return recipes


def load_recipes():
    if not RECIPES_FILE.exists():
        return None
    with open(RECIPES_FILE, encoding="utf-8") as f:
        return json.load(f)["recipes"]


def recipes_age_days():
    """Alter der Rezept-Datenbank in Tagen, None wenn keine vorhanden."""
    if not RECIPES_FILE.exists():
        return None
    try:
        with open(RECIPES_FILE, encoding="utf-8") as f:
            return (time.time() - json.load(f).get("built_at", 0)) / 86400
    except (OSError, ValueError):
        return None


def known_recipe_ids(api, cfg):
    """Rezepte, die der eingetragene Charakter kennt. None = nicht verfügbar."""
    char = cfg["character"]
    if not char.get("name"):
        return None
    realm = char.get("realm_slug") or cfg["realm"].get("slug")
    data = api.character_professions(realm, char["name"])
    if not data:
        print("Hinweis: Berufe des Charakters nicht abrufbar (Name/Realm prüfen) – zeige alle Rezepte.")
        return None
    known = set()
    for group in ("primaries", "secondaries"):
        for prof in data.get(group, []) or []:
            for tier in prof.get("tiers", []) or []:
                for rec in tier.get("known_recipes", []) or []:
                    known.add(rec["id"])
    return known
