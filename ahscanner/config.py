"""Konfiguration laden (config.toml) und mit Standardwerten zusammenführen."""
import copy
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # ältere Python-Versionen: kompatibles Paket "tomli"
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        print("Fehler: Paket 'tomli' fehlt. Bitte 1_einrichten.bat erneut starten.")
        sys.exit(1)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"
RECIPES_FILE = DATA_DIR / "recipes.json"
DB_FILE = DATA_DIR / "auktionshaus.sqlite"

DEFAULTS = {
    "blizzard": {
        "client_id": "",
        "client_secret": "",
        "region": "eu",
        "locale": "de_DE",
        "requests_per_second": 25,
    },
    "realm": {"slug": ""},
    "character": {"name": "", "realm_slug": ""},
    "filter": {
        "professions": [],
        "tier_keywords": [],
        "tier_exclude_keywords": [],
        "category_keywords": [],
        "only_known_recipes": False,
    },
    "analysis": {
        "ah_cut": 0.05,
        "market_share": 0.2,
        "history_days": 7,
        "retention_days": 30,
        "max_gap_hours": 3,
        "min_profit_gold": 5,
        "top_n": 50,
        "crafts_per_recipe": 10,
        "farm_list_recipes": 15,
        "competition_threshold": 40,
        "min_auctions": 3,
        "min_sold_per_day": 0.5,
        "min_demand_hours": 12,
        "demand_min_hours": 3,
        "easy_max_invest_gold": 5000,
        "only_recipe_items": False,
        "easy_min_sold_per_day": 2,
        "capital_threshold_gold": 50000,
    },
    "watch": {"interval_minutes": 60, "report_every_scan": True},
    "vendor_prices": {},
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None):
    cfg_path = Path(path) if path else BASE_DIR / "config.toml"
    if not cfg_path.exists():
        print(f"Konfigurationsdatei nicht gefunden: {cfg_path}")
        print("Kopiere 'config.example.toml' nach 'config.toml' und trage deine Daten ein.")
        sys.exit(1)
    with open(cfg_path, "rb") as f:
        user = tomllib.load(f)
    cfg = _merge(copy.deepcopy(DEFAULTS), user)
    # Secrets können per Umgebungsvariable kommen (GitHub Actions), nie im Repo
    import os
    if os.environ.get("AHSCANNER_CLIENT_ID"):
        cfg["blizzard"]["client_id"] = os.environ["AHSCANNER_CLIENT_ID"]
    if os.environ.get("AHSCANNER_CLIENT_SECRET"):
        cfg["blizzard"]["client_secret"] = os.environ["AHSCANNER_CLIENT_SECRET"]
    # Händlerpreise: Schlüssel als String, Werte in Kupfer
    cfg["vendor_prices"] = {str(k): int(v) for k, v in cfg.get("vendor_prices", {}).items()}
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    return cfg
