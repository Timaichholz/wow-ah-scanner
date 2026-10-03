"""Item-Metadaten (Name, Klasse, Bindung …) und Namenssuche – mit Cache in der Datenbank."""
import time
from concurrent.futures import ThreadPoolExecutor

# Handwerkswaren (item_class 7): Unterklasse -> womit man das Material bekommt
ACTIVITY_BY_SUBCLASS = {
    5: ("Stoff", "Humanoide farmen (jede Klasse, kein Beruf nötig)"),
    6: ("Leder", "Kürschnerei"),
    7: ("Erz & Stein", "Bergbau"),
    8: ("Fleisch & Fisch", "Tiere farmen / Angeln"),
    9: ("Kräuter", "Kräuterkunde"),
    10: ("Elementar", "Elementare farmen"),
    12: ("Verzauberung", "Entzaubern (Verzauberkunst)"),
    4: ("Juwelen", "Juwelierskunst / Bergbau"),
    16: ("Inschrift", "Mahlen (Inschriftenkunde)"),
    1: ("Bauteile", "Ingenieurskunst"),
    11: ("Sonstiges", "verschiedene Quellen"),
}
TRADE_GOODS_CLASS = 7


def _parse_item(data, fallback_id=None):
    def nm(v):
        if isinstance(v, dict):
            return v.get("de_DE") or v.get("en_US") or next(iter(v.values()), None)
        return v

    def en(v):
        return v.get("en_US") if isinstance(v, dict) else None

    preview = data.get("preview_item") or {}
    binding = (preview.get("binding") or {}).get("type")
    return {
        "item_id": data.get("id", fallback_id),
        "name": nm(data.get("name")),
        "name_en": en(data.get("name")),
        "quality": (data.get("quality") or {}).get("type"),
        "class_id": (data.get("item_class") or {}).get("id"),
        "class_name": nm((data.get("item_class") or {}).get("name")),
        "subclass_id": (data.get("item_subclass") or {}).get("id"),
        "subclass_name": nm((data.get("item_subclass") or {}).get("name")),
        "item_level": data.get("level"),
        "required_level": data.get("required_level"),
        "binding": binding,
        "equippable": 1 if data.get("is_equippable") else 0,
        "vendor_buy": data.get("purchase_price"),
        "vendor_sell": data.get("sell_price"),
        "fetched_at": int(time.time()),
    }


def ensure_items(api, db, ids, max_new=1500, workers=8):
    """Lädt fehlende Item-Metadaten nach (höchstens max_new pro Lauf). Gibt alle bekannten zurück."""
    ids = {int(i) for i in ids if i}
    known = db.get_items(ids)
    missing = [i for i in ids if i not in known][:max_new]
    if missing and api is not None:
        fetched = []

        def load(item_id):
            try:
                data = api.item(item_id)
            except Exception:  # noqa: BLE001 – Netzwerkfehler: nicht cachen, nächster Lauf versucht es erneut
                return None
            if not data:
                # Blizzard kennt das Item nicht -> Platzhalter merken, damit es nicht jedes Mal neu abgefragt wird
                return {"item_id": item_id, "fetched_at": int(time.time())}
            return _parse_item(data, item_id)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for res in pool.map(load, missing):
                if res and res.get("item_id"):
                    fetched.append(res)
        if fetched:
            db.put_items(fetched)
            known.update({f["item_id"]: f for f in fetched})
        print(f"Item-Infos: {len(fetched)} neu geladen, {len(known)} bekannt.")
    return known


SEARCH_CACHE_VERSION = "v2:"  # erhöhen, wenn sich die Suchlogik ändert -> alte Fehltreffer werden neu gesucht


def resolve_names(api, db, names, max_pages=5):
    """Englische Itemnamen -> Liste von Item-IDs (mehrere bei Qualitätsstufen). Ergebnisse werden gecacht."""
    result = {}
    for name in names:
        key = name.strip().lower()
        cached = db.get_search(SEARCH_CACHE_VERSION + key)
        if cached is not None:
            result[name] = [int(x) for x in str(cached[0] or "").split(",") if x]
            continue
        ids = []
        failed = api is None
        # Erst mit dem vollen Namen suchen, dann mit dem längsten (seltensten) Wort als Ausweichsuche
        words = sorted((w.strip("'\":,") for w in name.split()), key=len, reverse=True)
        queries = [name] + ([words[0]] if words and words[0].lower() != key else [])
        for query in queries:
            if ids or failed:
                break
            page, pages = 1, 1
            while page <= min(pages, max_pages):
                try:
                    res = api.item_search(query, page=page)
                except Exception:  # noqa: BLE001
                    res = None
                if res is None:
                    failed = True
                    break
                pages = res.get("pages", 1) or 1
                for hit in res.get("results", []):
                    data = hit.get("data") or {}
                    n = data.get("name")
                    en = n.get("en_US") if isinstance(n, dict) else n
                    if en and en.strip().lower() == key:
                        ids.append(int(data["id"]))
                if ids:
                    break  # exakter Treffer gefunden -> weitere Seiten unnötig
                page += 1
        if not failed:
            db.put_search(SEARCH_CACHE_VERSION + key, ",".join(str(i) for i in sorted(set(ids))))
        result[name] = sorted(set(ids))
    return result


def activity_for(item):
    if not item or item.get("class_id") != TRADE_GOODS_CLASS:
        return None
    return ACTIVITY_BY_SUBCLASS.get(item.get("subclass_id"), ("Sonstiges", "verschiedene Quellen"))
