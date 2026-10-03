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


def ensure_items(api, db, ids, max_new=800, workers=8):
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


def resolve_names(api, db, names):
    """Englische Itemnamen -> Liste von Item-IDs (mehrere bei Qualitätsstufen). Ergebnisse werden gecacht."""
    result = {}
    for name in names:
        key = name.strip().lower()
        cached = db.get_search(key)
        if cached is not None:
            result[name] = [int(x) for x in str(cached[0] or "").split(",") if x]
            continue
        ids = []
        if api is not None:
            try:
                hits = api.item_search(name)
            except Exception:  # noqa: BLE001
                hits = None
            if hits is None:
                result[name] = []
                continue  # Fehler -> nicht cachen, nächster Lauf versucht es erneut
            for hit in hits:
                data = hit.get("data") or {}
                n = data.get("name")
                en = n.get("en_US") if isinstance(n, dict) else n
                if en and en.strip().lower() == key:
                    ids.append(int(data["id"]))
            db.put_search(key, ",".join(str(i) for i in sorted(set(ids))))
        result[name] = sorted(set(ids))
    return result


def activity_for(item):
    if not item or item.get("class_id") != TRADE_GOODS_CLASS:
        return None
    return ACTIVITY_BY_SUBCLASS.get(item.get("subclass_id"), ("Sonstiges", "verschiedene Quellen"))
