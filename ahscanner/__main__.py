"""Kommandozeile: python -m ahscanner <befehl>"""
import argparse
import sys
import time
import webbrowser

from . import __version__
from .analysis import MarketView, analyze_materials, analyze_recipes, filter_recipes, money
from .api import ApiError, BlizzardAPI
from .config import DB_FILE, load_config
from .db import DB
from .recipes import build_cache, known_recipe_ids, load_recipes
from .report import write_reports
from .scan import run_scan


def cmd_setup(cfg, args):
    api = BlizzardAPI(cfg)
    api.token()
    print("✓ Verbindung zur Blizzard-API funktioniert.")
    slug = cfg["realm"].get("slug")
    if slug:
        print(f"✓ Realm '{slug}' gefunden (Connected-Realm-ID {api.connected_realm_id(slug)}).")
    else:
        print("– Kein Realm eingetragen: Es werden nur regionsweite Rohstoffe gescannt.")
    if cfg["character"].get("name"):
        known = known_recipe_ids(api, cfg)
        if known is not None:
            print(f"✓ Charakter gefunden: {len(known)} bekannte Rezepte.")
    print("\nNächster Schritt: 2_rezepte_laden.bat (einmalig, dauert einige Minuten).")


def cmd_recipes(cfg, args):
    api = BlizzardAPI(cfg)
    print("Baue Rezept-Datenbank aus der Blizzard-API ...")
    build_cache(api)


def _scan_once(cfg):
    api = BlizzardAPI(cfg)
    db = DB(DB_FILE)
    try:
        return run_scan(api, cfg, db)
    finally:
        db.close()


def cmd_scan(cfg, args):
    _scan_once(cfg)
    print("\nTipp: Für Nachfragedaten mehrere Scans im Abstand von ~1 Stunde (4_dauerscan.bat).")


def notice(msg, level="notice"):
    """Auf GitHub Actions als Annotation sichtbar machen, lokal einfach ausgeben."""
    import os
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level} title=AH-Scanner::{msg}")
    else:
        print(msg)


def _optional(label, func, default):
    """Optionale Auswertungen dürfen den Bericht nie verhindern."""
    try:
        return func()
    except Exception as exc:  # noqa: BLE001
        notice(f"{label} fehlgeschlagen: {type(exc).__name__}: {exc}", "warning")
        return default


def cmd_report(cfg, args):
    from .housing import analyze_housing
    from .farm import (analyze_farmspots, analyze_flips, analyze_matrix, analyze_raw_materials, analyze_token,
                       analyze_transmog, analyze_volume, data_health)

    recipes = load_recipes()
    if recipes is None:
        print("Keine Rezept-Datenbank gefunden – zuerst 2_rezepte_laden.bat ausführen.")
        sys.exit(1)
    try:
        api = BlizzardAPI(cfg)
    except ApiError:
        api = None  # Bericht geht auch ohne API, nur ohne neue Item-Namen
    known = None
    if cfg["filter"].get("only_known_recipes") and api:
        try:
            known = known_recipe_ids(api, cfg)
        except ApiError as exc:
            print(f"Hinweis: {exc}")
    selected = filter_recipes(recipes, cfg, known)
    db = DB(DB_FILE)
    try:
        overview = db.snapshot_overview()
        if not overview:
            print("Noch keine AH-Daten – zuerst 3_scan.bat ausführen.")
            sys.exit(1)
        mv = MarketView(db, cfg)

        rows, has_demand, skipped, all_rows = analyze_recipes(selected, mv, cfg)
        excl = [k.lower() for k in cfg["filter"].get("tier_exclude_keywords") or []]
        material_recipes = [r for r in recipes if not any(k in (r["tier"] or "").lower() for k in excl)]
        materials = analyze_materials(material_recipes, mv, cfg)
        extra = {
            "token": _optional("WoW-Marke", lambda: analyze_token(db, cfg), None),
            "health": _optional("Datenstatus", lambda: data_health(db, mv), {}),
            "spots": _optional("Farmspots", lambda: analyze_farmspots(api, db, mv, cfg), []),
            "raw": _optional("Rohstoffe", lambda: analyze_raw_materials(api, db, mv, cfg, recipes), []),
            "transmog": _optional("Transmog", lambda: analyze_transmog(api, db, mv, cfg, recipes), []),
            "volume": _optional("Volumen", lambda: analyze_volume(api, db, mv, cfg, recipes), []),
            "flips": _optional("Schnäppchen", lambda: analyze_flips(api, db, mv, cfg, recipes), []),
            "matrix": _optional("Berufe-Matrix", lambda: analyze_matrix(api, db, mv, cfg, recipes, all_rows), None),
            "housing": _optional("Housing-Deko", lambda: analyze_housing(api, db, mv, cfg, material_recipes), None),
        }
    finally:
        db.close()

    html_path, farm_path = write_reports(rows, materials, has_demand, mv.demand_hours(), overview, cfg, skipped, extra)

    spots = extra["spots"] or []
    found = sum(1 for s in spots for l in s["loot"] if l["found"])
    total = sum(len(s["loot"]) for s in spots)
    tok = extra["token"]
    notice(f"Bericht: {len(rows)} Crafts, {len(spots)} Farmspots (Beute erkannt {found}/{total}), "
           f"{sum(len(g['items']) for g in extra['raw'] or [])} Rohstoffe in {len(extra['raw'] or [])} Gruppen, "
           f"{len(extra['transmog'] or [])} Transmog-Teile, {len(extra['volume'] or [])} Volumen-Items, "
           f"{len(extra['flips'] or [])} Schnäppchen, Nachfrage {mv.demand_hours():.1f} h, "
           f"Marke {money(tok['price']) if tok else '–'}")
    if spots:
        notice("Top-Spots: " + " | ".join(f"{s['name_de'][:40]}: {s['verdict']}" for s in spots[:3]))
    def _row(r):
        sold = f"{r['sold_per_day']:.0f}/T" if r["sold_per_day"] is not None else "?/T"
        pot = money(r["potential_per_day"]) if r["potential_per_day"] is not None else "–"
        return (f"{(r['item'] or '')[:28]} [{(r['tier'] or '')[:22]}] G {money(r['profit'])} K {money(r['cost'])} "
                f"{sold} Ang {r['supply']}/{r['n_auctions']} Pot {pot}")
    for typ in ("Easy Money", "Solide", "Zeitintensiv"):
        top = [r for r in rows if r["type"] == typ][:8]
        if top:
            notice(f"Top {typ}: " + " | ".join(_row(r) for r in top))
    hs = extra.get("housing") or {}
    if hs:
        cats = sorted({(x["profession"], x["category"]) for x in hs["rows"]})
        notice(f"Housing: {len(hs['rows'])} Deko-Items erkannt (Signale {hs['detect']}) | Kategorien: "
               + "; ".join(f"{p[:12]}/{c}" for p, c in cats[:25]))
        notice("Housing je Beruf: " + " | ".join(
            f"{p['profession']}: {p['recipes']} Rez, {p['profitable']} Gewinn, {p['selling']} verk., "
            f"real. {money(p['realistic_day'])}/T, Erw: "
            + ",".join(f"{e[:6]} {money(v)}" for e, v in sorted(p['expansions'].items(), key=lambda kv: -kv[1])[:4])
            for p in hs["professions"]))
        def _gain(x):
            return (x["profit"] or 0) * min((x["sold_per_day"] or 0) * 0.5, 1.0) if (x["profit"] or 0) > 0 else 0
        detail = sorted(hs["rows"], key=_gain, reverse=True)[:12]
        notice("Housing Detail: " + " || ".join(
            f"{x['item'][:30]} [{x['profession'][:6]}/{x['expansion'][:8]}] VK {money(x['sell_price'])} "
            f"K {money(x['cost'])}{'' if x['cost_complete'] else '+?'} {x['sold_per_day'] or 0:.2f}/T "
            f"Ang {x['supply']}/{x['n_auctions']} Mat: "
            + ", ".join(f"{r['qty']:g}x {r['name'][:18]}={money(r['unit_price']) if r['unit_price'] else 'n/a'}"
                        for r in x["reagents"])
            for x in detail))
        top = [x for x in hs["rows"] if x["mine"]][:8]
        if top:
            notice("Housing deine Berufe: " + " | ".join(
                f"{x['item'][:26]} [{x['expansion']}] G {money(x['profit']) if x['profit'] is not None else '?'} "
                f"{(str(round(x['sold_per_day'])) if x['sold_per_day'] is not None else '?')}/T "
                f"Ang {x['supply']}/{x['n_auctions']}" for x in top))
    from .farm import UNMAPPED_TIERS
    mx = extra.get("matrix") or {}
    exps = [e["expansion"] for e in mx.get("expansions", [])]
    notice(f"Matrix-Erweiterungen: {', '.join(exps) or '–'} | nicht zugeordnete Stufen: "
           f"{'; '.join(sorted(UNMAPPED_TIERS)[:12]) or 'keine'}")

    print(f"\n{len(selected):,} Rezepte geprüft, {len(rows)} profitable gefunden.".replace(",", "."))
    print(f"Aussortiert: {skipped['thin']} Einzelangebote ohne Verkaufsnachweis, "
          f"{skipped['no_sales']} ohne Verkäufe im Zeitraum.")
    if mv.demand_hours() < float(cfg["analysis"].get("min_demand_hours", 12)):
        print(f"ACHTUNG: Erst {mv.demand_hours():.0f} h Nachfragedaten – Ergebnis noch vorläufig. "
              "Dauerscan mindestens 12–24 h laufen lassen.")
    for i, r in enumerate(rows[:10], 1):
        sold = "–" if r["sold_per_day"] is None else f"{r['sold_per_day']:.1f}/Tag"
        print(f"{i:>2}. {r['item'][:40]:<40} Gewinn {money(r['profit']):>10}  Verkäufe {sold:>10}  {r['rating']}")
    print(f"\nBericht:   {html_path}")
    print(f"Farmliste: {farm_path}")
    if not args.no_open:
        webbrowser.open(html_path.as_uri())


def cmd_watch(cfg, args):
    minutes = int(cfg["watch"]["interval_minutes"])
    print(f"Dauerscan alle {minutes} Minuten. Beenden mit Strg+C.\n")
    while True:
        print(f"[{time.strftime('%H:%M')}] Scan startet")
        try:
            result = _scan_once(cfg)
            if result and cfg["watch"].get("report_every_scan"):
                args.no_open = True
                cmd_report(cfg, args)
        except (ApiError, OSError) as exc:
            print(f"Fehler beim Scan: {exc} – neuer Versuch beim nächsten Durchlauf.")
        except SystemExit:
            pass
        time.sleep(minutes * 60)


def cmd_ci(cfg, args):
    """Ein kompletter Durchlauf für GitHub Actions: Rezepte (wöchentlich), Scan, Bericht, Webseite."""
    import shutil
    from .config import OUTPUT_DIR
    from .recipes import recipes_age_days

    age = recipes_age_days()
    if age is None or age > 7:
        print("Rezept-Datenbank fehlt oder ist älter als 7 Tage – wird neu geladen ...")
        build_cache(BlizzardAPI(cfg))
    results = _scan_once(cfg) or {}
    if results:
        notice("Scan: " + " | ".join(
            f"{src}: {r['auctions']} Auktionen, Lücke {r['interval_h']:.1f} h, ~{r['sold_units']} verkauft"
            if r["interval_h"] is not None else f"{src}: {r['auctions']} Auktionen, kein Vergleich (erste Daten/Lücke zu groß)"
            for src, r in results.items()))
    else:
        notice("Scan: keine neuen Daten von Blizzard seit dem letzten Lauf.")
    args.no_open = True
    cmd_report(cfg, args)

    site = OUTPUT_DIR / "site"
    site.mkdir(exist_ok=True)
    shutil.copy(OUTPUT_DIR / "bericht_aktuell.html", site / "index.html")
    shutil.copy(OUTPUT_DIR / "farmliste_aktuell.txt", site / "farmliste_aktuell.txt")
    if (OUTPUT_DIR / "daten.json").exists():
        shutil.copy(OUTPUT_DIR / "daten.json", site / "daten.json")
    print(f"Webseite vorbereitet: {site}")


def main():
    parser = argparse.ArgumentParser(prog="ahscanner", description=f"WoW AH-Scanner {__version__}")
    parser.add_argument("--config", help="Pfad zur config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Verbindung, Realm und Charakter prüfen")
    sub.add_parser("recipes", help="Rezept-Datenbank laden (einmalig)")
    sub.add_parser("scan", help="Einen AH-Snapshot speichern")
    sub.add_parser("watch", help="Dauerscan im eingestellten Intervall")
    rp = sub.add_parser("report", help="Bericht und Farmliste erzeugen")
    rp.add_argument("--no-open", action="store_true", help="Bericht nicht automatisch öffnen")
    sub.add_parser("ci", help="Kompletter Durchlauf für GitHub Actions")
    args = parser.parse_args()
    if not hasattr(args, "no_open"):
        args.no_open = True

    cfg = load_config(args.config)
    commands = {"setup": cmd_setup, "recipes": cmd_recipes, "scan": cmd_scan, "watch": cmd_watch,
                "report": cmd_report, "ci": cmd_ci}
    try:
        commands[args.command](cfg, args)
    except ApiError as exc:
        print(f"Fehler: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
