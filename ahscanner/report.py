"""Berichte erzeugen: HTML-Übersicht, CSV-Dateien und die Farmliste für Claude."""
import csv
import html
import time
from datetime import datetime

from .analysis import money
from .config import OUTPUT_DIR


def _num(v, digits=1):
    if v is None:
        return "–"
    return f"{v:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v):
    if v is None:
        return "–"
    return f"{v * 100:+.0f} %"


def _cell(text, sort_value=None, cls=""):
    sv = "" if sort_value is None else f' data-v="{sort_value}"'
    c = f' class="{cls}"' if cls else ""
    return f"<td{sv}{c}>{html.escape(str(text))}</td>"


CSS = """
:root{--bg:#f6f4ef;--fg:#1d1b18;--muted:#6b665e;--line:#ddd7cc;--card:#fff;--good:#1f7a4a;--warn:#a1620b;--bad:#a8322b;--accent:#7a4fb5}
@media (prefers-color-scheme:dark){:root{--bg:#16151a;--fg:#ece8e1;--muted:#9c968c;--line:#2e2c33;--card:#1e1d23;--good:#5cc28c;--warn:#e0a54a;--bad:#e4766e;--accent:#b28ce8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1400px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 8px}
.meta{color:var(--muted);margin-bottom:16px}.note{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);padding:10px 14px;border-radius:6px;margin:12px 0}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:900px}th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);cursor:pointer;font-weight:600;user-select:none}th:hover{color:var(--accent)}
td.num{text-align:right;font-variant-numeric:tabular-nums}td.wrapcell{white-space:normal;min-width:220px;color:var(--muted);font-size:12px}
.good{color:var(--good);font-weight:600}.warn{color:var(--warn)}.bad{color:var(--bad)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:8px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.ctitle{font-weight:700;font-size:15px;margin-bottom:4px}.muted{color:var(--muted);font-weight:400}.small{font-size:12px}
.card ol{margin:8px 0 0;padding-left:18px}.card li{margin:2px 0}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
.filters button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 12px;cursor:pointer}
.filters button.on{border-color:var(--accent);color:var(--accent);font-weight:600}
"""

JS = """
document.querySelectorAll('table').forEach(t=>{t.querySelectorAll('th').forEach((th,i)=>{th.addEventListener('click',()=>{
const b=t.tBodies[0],rows=[...b.rows],asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
rows.sort((x,y)=>{const a=x.cells[i],c=y.cells[i];const va=a.dataset.v??a.textContent,vc=c.dataset.v??c.textContent;
const na=parseFloat(va),nc=parseFloat(vc);if(!isNaN(na)&&!isNaN(nc))return asc?na-nc:nc-na;return asc?va.localeCompare(vc):vc.localeCompare(va)});
rows.forEach(r=>b.appendChild(r))})})});
document.querySelectorAll('.filters button').forEach(btn=>btn.addEventListener('click',()=>{
document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));btn.classList.add('on');
const f=btn.dataset.f;document.querySelectorAll('#crafts tbody tr').forEach(r=>{r.style.display=(!f||r.dataset.type===f)?'':'none'})}));
"""


def _rating_class(rating):
    if rating.startswith("Top") or rating == "Gut":
        return "good"
    if rating in ("Kaum Nachfrage", "Überangebot"):
        return "bad"
    return "warn"


TYPE_ORDER = ["Easy Money", "Easy Money?", "Solide", "Zeitintensiv", "Kapitalintensiv", "Zeit + Kapital"]
TYPE_CLASS = {"Easy Money": "good", "Easy Money?": "good", "Zeitintensiv": "warn", "Kapitalintensiv": "warn",
              "Zeit + Kapital": "bad"}
TYPE_HELP = {
    "Easy Money": "Alle Materialien im AH kaufbar, wenig Startkapital, verkauft sich regelmäßig.",
    "Easy Money?": "Wie Easy Money, aber noch ohne Nachfragedaten – erst bestätigen lassen.",
    "Solide": "Kaufbare Materialien, mittlerer Einsatz oder langsamer Absatz.",
    "Zeitintensiv": "Mindestens ein Material muss selbst gefarmt werden.",
    "Kapitalintensiv": "Hoher Goldeinsatz pro Charge – nur mit Polster angehen.",
    "Zeit + Kapital": "Farmen UND viel Gold nötig.",
}


def _summary(rows):
    cards = []
    for typ in TYPE_ORDER:
        items = [r for r in rows if r["type"] == typ]
        if not items:
            continue
        top = "".join(f"<li>{html.escape(r['item'])} <span class='muted'>· {money(r['profit'])}/Craft</span></li>"
                      for r in items[:3])
        cards.append(f"<div class='card'><div class='ctitle {TYPE_CLASS.get(typ, '')}'>{typ} "
                     f"<span class='muted'>({len(items)})</span></div><div class='muted small'>{TYPE_HELP[typ]}</div>"
                     f"<ol>{top}</ol></div>")
    buttons = "".join(f"<button data-f=\"{t}\">{t}</button>" for t in TYPE_ORDER if any(r["type"] == t for r in rows))
    return (f"<div class='cards'>{''.join(cards)}</div>"
            f"<div class='filters'><button data-f='' class='on'>Alle</button>{buttons}</div>")


def _recipe_table(rows):
    head = ["#", "Typ", "Item", "Beruf", "Erweiterung", "Bewertung", "Verkauf", "Kosten", "Gewinn/Craft", "Marge",
            "Einsatz/Charge", "Angebot", "Angebote", "Verkauft/Tag", "Reicht Tage", "Potenzial/Tag", "Preis vs. Ø", "Hinweise"]
    out = ["<table id='crafts'><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"]
    for i, r in enumerate(rows, 1):
        out.append(f"<tr data-type=\"{html.escape(r['type'])}\">" + "".join([
            _cell(i, i, "num"),
            _cell(r["type"], cls=TYPE_CLASS.get(r["type"], "")),
            _cell(r["item"]),
            _cell(r["profession"]),
            _cell(r["tier"]),
            _cell(r["rating"], cls=_rating_class(r["rating"])),
            _cell(money(r["sell_price"]), r["sell_price"], "num"),
            _cell(money(r["cost"]), r["cost"], "num"),
            _cell(money(r["profit"]), r["profit"], "num good"),
            _cell(_pct(r["margin"]), r["margin"] if r["margin"] is not None else -1, "num"),
            _cell(money(r["invest"]), r["invest"], "num"),
            _cell(_num(r["supply"], 0), r["supply"], "num"),
            _cell(r["n_auctions"], r["n_auctions"], "num"),
            _cell(_num(r["sold_per_day"]), r["sold_per_day"] if r["sold_per_day"] is not None else -1, "num"),
            _cell(_num(r["days_supply"]), r["days_supply"] if r["days_supply"] is not None else 9999, "num"),
            _cell(money(r["potential_per_day"]), r["potential_per_day"] if r["potential_per_day"] is not None else -1, "num"),
            _cell(_pct(r["trend"]), r["trend"] if r["trend"] is not None else 0, "num"),
            _cell("; ".join(r["flags"]) or "–", cls="wrapcell"),
        ]) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _material_table(rows):
    head = ["Material", "Marktpreis", "Angebot", "Angebote", "Verkauft/Tag", "Gold-Umsatz/Tag", "Reicht Tage", "Preis vs. Ø"]
    out = ["<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>" + "".join([
            _cell(r["item"]),
            _cell(money(r["price"]), r["price"], "num"),
            _cell(_num(r["supply"], 0), r["supply"], "num"),
            _cell(r["n_auctions"], r["n_auctions"], "num"),
            _cell(_num(r["sold_per_day"]), r["sold_per_day"] if r["sold_per_day"] is not None else -1, "num"),
            _cell(money(r["gold_volume"]), r["gold_volume"] if r["gold_volume"] is not None else -1, "num"),
            _cell(_num(r["days_supply"]), r["days_supply"] if r["days_supply"] is not None else 9999, "num"),
            _cell(_pct(r["trend"]), r["trend"] if r["trend"] is not None else 0, "num"),
        ]) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def write_html(path, recipes, materials, has_demand, demand_hours, overview, skipped):
    scans = ", ".join(f"{src}: {cnt} Scans" for src, cnt, *_ in overview) or "keine"
    notes = []
    if skipped.get("thin") or skipped.get("no_sales"):
        notes.append(f"<div class='note'>Aussortiert: <b>{skipped.get('thin', 0)}</b> Einzelangebote mit unsicherem Preis "
                     f"(zu wenige Angebote und kein Verkaufsnachweis) und <b>{skipped.get('no_sales', 0)}</b> Items ohne "
                     "Verkäufe im Zeitraum.</div>")
    if not has_demand:
        notes.append("<div class='note'><b>Noch keine Nachfragedaten.</b> Die Verkäufe werden aus dem Vergleich "
                     "zweier Scans geschätzt. Lass den Dauerscan ein paar Stunden laufen (ideal: 1–2 Tage), "
                     "bis dahin ist die Liste nur nach Gewinn pro Craft sortiert.</div>")
    elif demand_hours < 24:
        notes.append(f"<div class='note'>Nachfragedaten decken erst {demand_hours:.0f} Stunden ab – "
                     "Tageszeit-Schwankungen sind noch nicht ausgeglichen.</div>")
    doc = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AH-Scanner Bericht</title><style>{CSS}</style></head>
<body><main><h1>AH-Scanner – Was lohnt sich zu craften?</h1>
<div class="meta">Erstellt {datetime.now():%d.%m.%Y %H:%M} · {scans} · Nachfrage-Zeitraum: {demand_hours:.0f} h · <a href="farmliste_aktuell.txt">Farmliste (Text)</a></div>
{''.join(notes)}
<div class="note"><b>So liest du die Tabelle:</b> <i>Potenzial/Tag</i> = Gewinn pro Stück × geschätzte Verkäufe pro Tag ×
dein angenommener Marktanteil. <i>Reicht Tage</i> = wie lange das aktuelle Angebot bei der jetzigen Nachfrage hält
(klein = knapp = gut). Spaltenköpfe anklicken zum Sortieren.</div>
<h2>Empfohlene Crafts</h2>{_summary(recipes)}<div class="wrap">{_recipe_table(recipes)}</div>
<h2>Materialien mit dem meisten Umsatz (Farm-Ziele)</h2><div class="wrap">{_material_table(materials)}</div>
</main><script>{JS}</script></body></html>"""
    path.write_text(doc, encoding="utf-8")


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(fields)
        for r in rows:
            w.writerow(["; ".join(r[k]) if isinstance(r.get(k), list) else r.get(k) for k in fields])


def write_farm_list(path, recipes, cfg, demand_hours):
    crafts = int(cfg["analysis"]["crafts_per_recipe"])
    limit = int(cfg["analysis"]["farm_list_recipes"])
    lines = [
        "AH-SCANNER FARMLISTE",
        f"Erstellt: {datetime.now():%d.%m.%Y %H:%M} | Nachfrage-Zeitraum: {demand_hours:.0f} h | Region: {cfg['blizzard']['region'].upper()}",
        f"Mengen jeweils für {crafts} Crafts. Diese Datei kannst du Claude geben, um Farm-Routen zu planen.",
        "",
    ]
    totals = {}
    for i, r in enumerate(recipes[:limit], 1):
        sold = "–" if r["sold_per_day"] is None else f"{r['sold_per_day']:.1f}"
        lines.append(f"{i}. {r['item']}  [{r['profession']} | {r['tier']}]  TYP: {r['type']}")
        lines.append(f"   Gewinn/Craft: {money(r['profit'])} | Verkauf: {money(r['sell_price'])} | Einsatz/Charge: {money(r['invest'])} | "
                     f"Verkauft/Tag: {sold} | Angebot: {r['supply']} | Bewertung: {r['rating']}")
        for rg in r["reagents"]:
            qty = rg["qty"] * crafts
            if rg["origin"] == "farmen":
                how = "FARMEN (nicht im AH)"
            elif rg["origin"] == "Händler":
                how = f"Händler ~{money(rg['unit_price'])}/Stk"
            else:
                how = f"AH ~{money(rg['unit_price'])}/Stk"
            lines.append(f"   - {qty:>5} x {rg['name']}  ({how})")
            t = totals.setdefault(rg["id"], {"name": rg["name"], "qty": 0, "origin": rg["origin"], "price": rg["unit_price"]})
            t["qty"] += qty
        if r["flags"]:
            lines.append(f"   Hinweise: {'; '.join(r['flags'])}")
        lines.append("")
    lines.append("GESAMTBEDARF (alle Rezepte oben)")
    for t in sorted(totals.values(), key=lambda x: -(x["qty"] * (x["price"] or 0))):
        value = "Wert unbekannt" if not t["price"] else f"Wert ~{money(t['qty'] * t['price'])}"
        lines.append(f"- {t['qty']:>6} x {t['name']}  [{t['origin']}] {value}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_reports(recipes, materials, has_demand, demand_hours, overview, cfg, skipped=None):
    stamp = time.strftime("%Y-%m-%d_%H%M")
    html_path = OUTPUT_DIR / f"bericht_{stamp}.html"
    write_html(html_path, recipes, materials, has_demand, demand_hours, overview, skipped or {})
    write_csv(OUTPUT_DIR / f"crafts_{stamp}.csv", recipes,
              ["type", "item", "item_id", "recipe", "profession", "tier", "category", "rating", "sell_price", "cost", "profit",
               "invest", "supply", "n_auctions", "sold_per_day", "days_supply", "potential_per_day", "flags"])
    write_csv(OUTPUT_DIR / f"materialien_{stamp}.csv", materials,
              ["item", "item_id", "price", "supply", "n_auctions", "sold_per_day", "gold_volume", "days_supply", "trend"])
    farm_path = OUTPUT_DIR / f"farmliste_{stamp}.txt"
    write_farm_list(farm_path, recipes, cfg, demand_hours)
    # "latest"-Kopien, damit man immer weiß, welche Datei aktuell ist
    (OUTPUT_DIR / "bericht_aktuell.html").write_text(html_path.read_text(encoding="utf-8"), encoding="utf-8")
    (OUTPUT_DIR / "farmliste_aktuell.txt").write_text(farm_path.read_text(encoding="utf-8"), encoding="utf-8")
    return html_path, farm_path
