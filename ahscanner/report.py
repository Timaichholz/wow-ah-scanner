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
            f"<td>{_link(r.get('item_id'), r['item'])}</td>",
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


WOWHEAD = "https://www.wowhead.com/de/item={}"
TYPE_LABEL = {"transmog": "Transmog-Weltdrop", "raid": "Alter Raid", "cloth": "Stoff", "materials": "Materialien",
              "mixed": "Gemischt"}


def _e(v):
    return html.escape(str(v))


def _link(item_id, name):
    if not item_id:
        return _e(name)
    return f"<a href='{WOWHEAD.format(item_id)}' target='_blank' rel='noopener'>{_e(name)}</a>"


def _ts(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m. %H:%M") if ts else "–"


def _sold(v):
    return "–" if v is None else _num(v)


# ---------------------------------------------------------------------------
# Abschnitte
# ---------------------------------------------------------------------------
def _section_goal(tok):
    if not tok:
        return "<div class='card'><div class='ctitle'>WoW-Marke</div><div class='muted'>Noch kein Preis erfasst.</div></div>"
    trend = tok["vs_avg"]
    hint = ("gerade <b class='good'>günstiger</b> als im 7-Tage-Schnitt – guter Kaufzeitpunkt" if trend < -0.02 else
            "gerade <b class='warn'>teurer</b> als im 7-Tage-Schnitt – wenn möglich warten" if trend > 0.02 else
            "liegt im 7-Tage-Schnitt")
    prog = ""
    if tok["have"]:
        pct = min(100, tok["have"] / tok["need"] * 100) if tok["need"] else 0
        prog = (f"<div class='bar'><span style='width:{pct:.0f}%'></span></div>"
                f"<div class='small muted'>{money(tok['have'])} von {money(tok['need'])} ({pct:.0f} %) · "
                f"fehlen {money(tok['missing'])}</div>")
    return (f"<div class='card'><div class='ctitle'>Ziel: {_e(tok['label'])}</div>"
            f"<div class='big'>{money(tok['need'])}</div>"
            f"<div class='small muted'>{tok['tokens']} WoW-Marken × {money(tok['price'])} (Stand {_ts(tok['updated'])})</div>"
            f"{prog}<div class='small'>Markenpreis {hint}. 7 Tage: {money(tok['min7'])} – {money(tok['max7'])}</div></div>")


def _section_health(health, skipped):
    parts = []
    names = {"commodity": "Rohstoffe", "realm": "Realm (Ausrüstung)"}
    warn = False
    for src, h in health.get("sources", {}).items():
        gap_cls = "bad" if h["max_gap"] > 4 else "warn" if h["max_gap"] > 2 else "good"
        warn = warn or h["max_gap"] > 4 or h["age_h"] > 3
        parts.append(f"<li>{names.get(src, src)}: {h['scans_48h']} Scans in 48 h, letzter {_ts(h['last'])}, "
                     f"größte Lücke <span class='{gap_cls}'>{h['max_gap']:.1f} h</span></li>")
    dh = health.get("demand_hours", 0)
    status = ("<b class='good'>belastbar</b>" if dh >= 24 else "<b class='warn'>vorläufig</b>" if dh >= 3
              else "<b class='bad'>noch keine</b>")
    return (f"<div class='card'><div class='ctitle'>Datenstatus</div>"
            f"<div>Nachfragedaten: {status} ({dh:.0f} h Verlauf)</div><ul class='small'>{''.join(parts)}</ul>"
            f"<div class='small muted'>Aussortiert: {skipped.get('thin', 0)} unsichere Einzelangebote</div>"
            + ("<div class='small bad'>Achtung: Lücken im Zeitplan – Verkaufszahlen unvollständig.</div>" if warn else "")
            + "</div>")


def _top_list(title, rows, fmt):
    items = "".join(f"<li>{fmt(r)}</li>" for r in rows) or "<li class='muted'>noch keine Daten</li>"
    return f"<div class='card'><div class='ctitle'>{title}</div><ol>{items}</ol></div>"


def _section_overview(extra, recipes, skipped):
    spots = extra.get("spots") or []
    raw = extra.get("raw") or []
    tm = extra.get("transmog") or []
    mine = [it for g in raw if g["mine"] for it in g["items"]]
    mine.sort(key=lambda it: it["gold_volume"] or 0, reverse=True)
    easy = [r for r in recipes if r["type"].startswith("Easy")][:5]
    cards = [
        _section_goal(extra.get("token")),
        _section_health(extra.get("health") or {}, skipped),
        _top_list("Beste Farmspots", spots[:3], lambda s: f"<a href='#spot-{_e(s['id'])}'>{_e(s['name_de'])}</a>"
                  f"<div class='small muted'>{_e(s['verdict'])} · Marktvolumen {money(s['market_volume_day'])}/Tag</div>"),
        _top_list("Rohstoffe, die du ohne Sammelberuf farmen kannst", mine[:5],
                  lambda it: f"{_link(it['item_id'], it['name'])} <span class='muted small'>· {money(it['price'])}"
                  f" · {_sold(it['sold_per_day'])}/Tag</span>"),
        _top_list("Transmog, der sich verkauft", tm[:5],
                  lambda it: f"{_link(it['item_id'], it['name'])} <span class='muted small'>· {money(it['price'])}"
                  f" · {_sold(it['sold_per_day'])}/Tag</span>"),
        _top_list("Easy-Money-Crafts", easy,
                  lambda r: f"{_link(r['item_id'], r['item'])} <span class='muted small'>· {_e(r['profession'])}"
                  f" · {money(r['profit'])}/Craft</span>"),
    ]
    return f"<section id='uebersicht'><h2>Übersicht</h2><div class='cards'>{''.join(cards)}</div></section>"


def _section_spots(spots):
    if not spots:
        return ""
    out = ["<section id='farmspots'><h2>Farmspots</h2>",
           "<div class='note'>Recherchierte Solo-Spots für Level 80 ohne Midnight. Bewertet wird die <b>Beute</b> am "
           "Markt: aktueller Preis, Angebot und geschätzte Verkäufe pro Tag (Dun Morogh / EU). Wie oft etwas droppt, "
           "liefert die Blizzard-API nicht – dafür stehen belegte Dropchancen und Quellen dabei. Echte Gold-pro-Stunde-"
           "Werte entstehen aus deinem Farm-Logbuch (<code>farm_log</code> in der Config).</div>"]
    for rank, s in enumerate(spots, 1):
        conf_cls = {"hoch": "good", "mittel": "warn"}.get(s.get("confidence"), "bad")
        verdict_cls = {"Beute verkauft sich": "good", "Kein Absatz gemessen": "bad",
                       "Beute aktuell nicht im AH": "bad"}.get(s["verdict"], "warn")
        rows = []
        for l in s["loot"]:
            name = _link(l["item_id"], l["name"]) if l["found"] else f"{_e(l['name_en'])} <span class='bad small'>(nicht gefunden)</span>"
            rows.append(
                f"<tr><td>{name}</td><td class='num'>{money(l['price'])}</td><td class='num'>{_num(l['supply'], 0)}</td>"
                f"<td class='num'>{l['n_auctions']}</td><td class='num'>{_sold(l['sold_per_day'])}</td>"
                f"<td class='wrapcell'>{_e(l['drop_rate'] or '–')}</td></tr>")
        gph = ""
        if s.get("measured_gph"):
            gph = (f"<div><b>Gemessen:</b> {money(s['measured_gph'])} pro Stunde "
                   f"({s['log']['sessions']} Sessions, {s['log']['minutes']:.0f} Min.)</div>")
        sources = " · ".join(f"<a href='{_e(u)}' target='_blank' rel='noopener'>Quelle {i}</a>"
                             for i, u in enumerate(s.get("sources", []), 1))
        out.append(f"""<div class='spot card' id='spot-{_e(s['id'])}'>
<div class='spothead'><span class='rank'>#{rank}</span><div><div class='ctitle'>{_e(s['name_de'])}</div>
<div class='small muted'>{_e(s.get('zone_de', ''))}{' · ' + _e(s['coords']) if s.get('coords') else ''} · {TYPE_LABEL.get(s.get('type'), _e(s.get('type', '')))}</div></div></div>
<div class='chips'><span class='chip {verdict_cls}'>{_e(s['verdict'])}</span>
<span class='chip'>Marktvolumen der Beute: {money(s['market_volume_day'])}/Tag</span>
<span class='chip'>Wertvollstes Teil: {money(s['top_value'])}</span>
<span class='chip {conf_cls}'>Verlässlichkeit: {_e(s.get('confidence', '?'))}</span></div>
{gph}
<div class='wrap'><table class='mini'><thead><tr><th>Beute</th><th>Preis</th><th>Angebot</th><th>Angebote</th><th>Verkauft/Tag</th><th>Dropchance</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<details><summary>Anleitung: Anreise, Mobs, Methode</summary>
<p><b>Anreise:</b> {_e(s.get('travel_de', ''))}</p><p><b>Mobs:</b> {_e(s.get('mobs', ''))}</p>
<p><b>Methode:</b> {_e(s.get('method_de', ''))}</p><p><b>Vorsicht:</b> {_e(s.get('caveats_de', ''))}</p>
<p class='small'>{sources}</p></details></div>""")
    out.append("</section>")
    return "".join(out)


def _section_raw(groups):
    if not groups:
        return ""
    out = ["<section id='rohstoffe'><h2>Rohstoffe nach Tätigkeit</h2>",
           "<div class='note'>Was sich am Markt bewegt, gruppiert danach, <b>wie</b> man es bekommt. "
           "<span class='good'>Grün markierte Gruppen</span> kannst du ohne Sammelberuf farmen. Midnight-Materialien "
           "sind ausgeblendet. <i>Gold-Umsatz/Tag</i> = Preis × geschätzte Verkäufe – je höher, desto sicherer "
           "wirst du deine Ware los.</div>"]
    head = "<tr><th>Material</th><th>Erweiterung</th><th>Preis</th><th>Angebot</th><th>Verkauft/Tag</th><th>Gold-Umsatz/Tag</th><th>Reicht Tage</th><th>Preis vs. Ø</th></tr>"
    for g in groups:
        rows = []
        for it in g["items"]:
            rows.append("<tr>" + "".join([
                f"<td>{_link(it['item_id'], it['name'])}</td>",
                _cell(it["expansion"]),
                _cell(money(it["price"]), it["price"], "num"),
                _cell(_num(it["supply"], 0), it["supply"], "num"),
                _cell(_sold(it["sold_per_day"]), it["sold_per_day"] if it["sold_per_day"] is not None else -1, "num"),
                _cell(money(it["gold_volume"]), it["gold_volume"] if it["gold_volume"] is not None else -1, "num"),
                _cell(_num(it["days_supply"]), it["days_supply"] if it["days_supply"] is not None else 9999, "num"),
                _cell(_pct(it["trend"]), it["trend"] if it["trend"] is not None else 0, "num"),
            ]) + "</tr>")
        badge = "<span class='chip good'>für dich machbar</span>" if g["mine"] else ""
        out.append(f"<h3>{_e(g['label'])} <span class='muted small'>· {_e(g['how'])}</span> {badge}</h3>"
                   f"<div class='wrap'><table>{head}<tbody>{''.join(rows)}</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def _section_transmog(rows):
    out = ["<section id='transmog'><h2>Transmog-Markt (Dun Morogh)</h2>",
           "<div class='note'>Tragbare Ausrüstung über 2.000 Gold, die <b>nicht</b> herstellbar ist – also Drops. "
           "Sobald genug Verlauf da ist, erscheinen hier nur Teile, die sich nachweislich verkaufen. "
           "Klick auf den Namen öffnet Wowhead: dort steht, wo das Teil droppt – so findest du neue Farmziele.</div>"]
    if not rows:
        out.append("<p class='muted'>Noch keine Daten.</p></section>")
        return "".join(out)
    head = "<tr><th>Item</th><th>Stufe</th><th>Preis</th><th>Angebot</th><th>Verkauft/Tag</th><th>Reicht Tage</th><th>Hinweis</th></tr>"
    body = []
    for it in rows:
        hint = "Preise stark gestreut" if (it["spread"] or 0) > 3 else ""
        body.append("<tr>" + "".join([
            f"<td>{_link(it['item_id'], it['name'])}</td>",
            _cell(it["required_level"] or "–", it["required_level"] or 0, "num"),
            _cell(money(it["price"]), it["price"], "num"),
            _cell(it["supply"], it["supply"], "num"),
            _cell(_sold(it["sold_per_day"]), it["sold_per_day"] if it["sold_per_day"] is not None else -1, "num"),
            _cell(_num(it["days_supply"]), it["days_supply"] if it["days_supply"] is not None else 9999, "num"),
            _cell(hint or "–", cls="wrapcell"),
        ]) + "</tr>")
    out.append(f"<div class='wrap'><table>{head}<tbody>{''.join(body)}</tbody></table></div></section>")
    return "".join(out)


EXTRA_CSS = """
nav{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);display:flex;gap:6px;flex-wrap:wrap;padding:8px 0;margin-bottom:8px}
nav a{border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:999px;padding:4px 12px;text-decoration:none;font-size:13px}
a{color:var(--accent)}h3{font-size:15px;margin:22px 0 6px}section{scroll-margin-top:56px}
.big{font-size:26px;font-weight:700;margin:4px 0}.bar{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:6px 0}
.bar span{display:block;height:100%;background:var(--good)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px}
.spot{margin:14px 0}.spothead{display:flex;gap:12px;align-items:center}.rank{font-size:22px;font-weight:700;color:var(--accent);min-width:42px}
table.mini{min-width:640px}details{margin-top:8px}summary{cursor:pointer;color:var(--accent)}details p{margin:6px 0}
code{background:var(--line);padding:0 4px;border-radius:3px}
@media (max-width:600px){h1{font-size:20px}.big{font-size:22px}}
"""


def write_html(path, recipes, materials, has_demand, demand_hours, overview, skipped, extra=None):
    extra = extra or {}
    scans = ", ".join(f"{ {'commodity': 'Rohstoffe', 'realm': 'Realm'}.get(src, src)}: {cnt} Scans"
                      for src, cnt, *_ in overview) or "keine"
    notes = []
    if demand_hours < 3:
        notes.append("<div class='note'><b>Noch keine Nachfragedaten.</b> Verkäufe werden aus dem Vergleich "
                     "aufeinanderfolgender Scans geschätzt. Bis dahin sind alle Listen nur nach Preis sortiert.</div>")
    elif demand_hours < 24:
        notes.append(f"<div class='note'>Nachfragedaten decken erst {demand_hours:.0f} Stunden ab – "
                     "Tageszeit-Schwankungen sind noch nicht ausgeglichen.</div>")
    doc = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Gold-Berater</title>
<style>{CSS}{EXTRA_CSS}</style></head>
<body><main><h1>Gold-Berater · Dun Morogh</h1>
<div class="meta">Erstellt {datetime.now():%d.%m.%Y %H:%M} · {scans} · Nachfrage-Zeitraum: {demand_hours:.0f} h · <a href="farmliste_aktuell.txt">Farmliste (Text)</a></div>
<nav><a href="#uebersicht">Übersicht</a><a href="#farmspots">Farmspots</a><a href="#rohstoffe">Rohstoffe</a><a href="#transmog">Transmog</a><a href="#crafting">Crafting</a></nav>
{''.join(notes)}
{_section_overview(extra, recipes, skipped)}
{_section_spots(extra.get('spots'))}
{_section_raw(extra.get('raw'))}
{_section_transmog(extra.get('transmog') or [])}
<section id='crafting'><h2>Crafting</h2>
<div class="note"><i>Potenzial/Tag</i> = Gewinn pro Stück × geschätzte Verkäufe pro Tag × angenommener Marktanteil.
<i>Reicht Tage</i> = wie lange das Angebot bei der jetzigen Nachfrage hält (klein = knapp = gut). Spaltenköpfe anklicken zum Sortieren.</div>
{_summary(recipes)}<div class="wrap">{_recipe_table(recipes)}</div></section>
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


def write_summary_json(path, recipes, extra, demand_hours):
    """Kompakte Zusammenfassung als JSON – zum Weiterverarbeiten (z. B. durch Claude)."""
    import json

    def slim_spot(s):
        base = {k: s.get(k) for k in ("id", "name_de", "verdict", "market_volume_day", "top_value", "n_selling",
                                      "confidence", "measured_gph")}
        base["loot"] = [{k: l.get(k) for k in ("name", "name_en", "item_id", "price", "supply", "sold_per_day")}
                        for l in s.get("loot", [])]
        return base

    data = {
        "erstellt": datetime.now().isoformat(timespec="minutes"),
        "nachfrage_stunden": round(demand_hours, 1),
        "marke": extra.get("token"),
        "datenstatus": extra.get("health"),
        "farmspots": [slim_spot(s) for s in (extra.get("spots") or [])],
        "rohstoffe": [{"gruppe": g["label"], "fuer_dich": g["mine"],
                       "items": [{k: it[k] for k in ("name", "item_id", "expansion", "price", "sold_per_day", "gold_volume")}
                                 for it in g["items"]]} for g in (extra.get("raw") or [])],
        "transmog": [{k: it[k] for k in ("name", "item_id", "price", "supply", "sold_per_day")}
                     for it in (extra.get("transmog") or [])],
        "crafts": [{k: r.get(k) for k in ("type", "item", "item_id", "profession", "tier", "profit", "sold_per_day",
                                          "invest", "rating")} for r in recipes[:40]],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def write_reports(recipes, materials, has_demand, demand_hours, overview, cfg, skipped=None, extra=None):
    stamp = time.strftime("%Y-%m-%d_%H%M")
    html_path = OUTPUT_DIR / f"bericht_{stamp}.html"
    write_html(html_path, recipes, materials, has_demand, demand_hours, overview, skipped or {}, extra)
    write_summary_json(OUTPUT_DIR / "daten.json", recipes, extra or {}, demand_hours)
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
