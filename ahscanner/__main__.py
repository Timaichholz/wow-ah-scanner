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


def cmd_report(cfg, args):
    recipes = load_recipes()
    if recipes is None:
        print("Keine Rezept-Datenbank gefunden – zuerst 2_rezepte_laden.bat ausführen.")
        sys.exit(1)
    known = None
    if cfg["filter"].get("only_known_recipes"):
        try:
            known = known_recipe_ids(BlizzardAPI(cfg), cfg)
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
    finally:
        db.close()

    rows, has_demand, skipped = analyze_recipes(selected, mv, cfg)
    excl = [k.lower() for k in cfg["filter"].get("tier_exclude_keywords") or []]
    material_recipes = [r for r in recipes if not any(k in (r["tier"] or "").lower() for k in excl)]
    materials = analyze_materials(material_recipes, mv, cfg)
    html_path, farm_path = write_reports(rows, materials, has_demand, mv.demand_hours(), overview, cfg, skipped)

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
    _scan_once(cfg)
    args.no_open = True
    cmd_report(cfg, args)

    site = OUTPUT_DIR / "site"
    site.mkdir(exist_ok=True)
    shutil.copy(OUTPUT_DIR / "bericht_aktuell.html", site / "index.html")
    shutil.copy(OUTPUT_DIR / "farmliste_aktuell.txt", site / "farmliste_aktuell.txt")
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
