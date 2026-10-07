"""Kompakte Kandidatenliste aller Goldquellen – für die Detailanalyse (Text, eine Zeile pro Kandidat)."""
from .analysis import money


def _g(c):
    return "-" if c is None else money(c)


def _f(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def build_candidates(all_rows, extra, mv, demand_hours):
    L = [f"KANDIDATEN | Nachfrage {demand_hours:.0f} h"]

    def src(item_id):
        st = mv.stats(item_id) or {}
        return {"commodity": "R", "realm": "S"}.get(st.get("source"), "?")

    # 1) Crafting: realistische Tagesmenge = min(25 % der Verkäufe, 20/Tag Region bzw. 2/Tag Realm)
    crafts = []
    for r in all_rows:
        sold = r.get("sold_per_day")
        if not sold or (r.get("profit") or 0) <= 0:
            continue
        m = src(r["item_id"])
        cap = 20 if m == "R" else 2
        units = min(sold * 0.25, cap)
        crafts.append((r["profit"] / max(r["crafted_qty"], 1) * units, m, r))
    crafts.sort(key=lambda x: -x[0])
    L.append("## CRAFT (real/Tag | Markt R=Region S=Realm | Beruf/Stufe | VK | Kosten | Gewinn | Verk/T | Angebot/Auk | Trend | Flags)")
    for real, m, r in crafts[:45]:
        L.append(f"{_g(real)} | {m} | {r['item'][:30]} | {r['profession'][:12]}/{(r['tier'] or '')[:24]} | VK {_g(r['sell_price'])} | "
                 f"K {_g(r['cost'])} | G {_g(r['profit'])} | {_f(sold_or(r))}/T | {r['supply']}/{r['n_auctions']} | "
                 f"{_f((r.get('trend') or 0) * 100, 0)}% | {'; '.join(r.get('flags') or [])[:80]}")

    hs = extra.get("housing") or {}
    deco = []
    for x in hs.get("rows") or []:
        if (x.get("profit") or 0) > 0 and x.get("sold_per_day"):
            deco.append((x["profit"] * min(x["sold_per_day"] * 0.5, 1.0), x))
    deco.sort(key=lambda t: -t[0])
    L.append("## DEKO (real/Tag | Item | Beruf/Erw | VK | Kosten | Gewinn | Verk/T | Angebot | Holz)")
    for real, x in deco[:30]:
        L.append(f"{_g(real)} | {x['item'][:30]} | {x['profession'][:12]}/{x['expansion'][:12]} | VK {_g(x['sell_price'])} | "
                 f"K {_g(x['cost'])}{'' if x['cost_complete'] else '+?'} | G {_g(x['profit'])} | {_f(x['sold_per_day'], 2)}/T | "
                 f"{x['supply']}/{x['n_auctions']} | {x.get('wood', 0):g}x {x.get('wood_name', '')}")

    L.append("## ROHSTOFF (Gruppe | Item | Erw | Preis | Verk/T | Umsatz/T | Angebot | Trend)")
    for g in extra.get("raw") or []:
        for it in g["items"][:8]:
            L.append(f"{g['label']} | {it['name'][:28]} | {it['expansion'][:10]} | {_g(it['price'])} | {_f(it['sold_per_day'], 0)} | "
                     f"{_g(it['gold_volume'])} | {it['supply']} | {_f((it['trend'] or 0) * 100, 0)}%")

    L.append("## TRANSMOG/BOE Realm (Item | Preis | Verk/T | Angebot)")
    for it in (extra.get("transmog") or [])[:25]:
        L.append(f"{it['name'][:34]} | {_g(it['price'])} | {_f(it['sold_per_day'], 2)} | {it['supply']}")

    L.append("## FARMSPOT (Spot | Urteil | Markt-Umsatz/T | Beute: Name Preis Verk/T)")
    for s in extra.get("spots") or []:
        loot = ", ".join(f"{(l['name'] or '')[:18]} {_g(l['price'])} {_f(l['sold_per_day'], 1)}" for l in s.get("loot", [])[:6])
        L.append(f"{s['name_de'][:40]} | {s['verdict']} | {_g(s.get('market_volume_day'))} | {loot}")

    L.append("## FLIP (Item | Markt | Kauf | Schnitt | Gewinn/Stk | Verk/T)")
    for f in (extra.get("flips") or [])[:15]:
        L.append(f"{f['name'][:30]} | {f['market']} | {_g(f['buy'])} | {_g(f['avg'])} | {_g(f['margin'])} | {_f(f['sold_per_day'], 0)}")
    return L


def sold_or(r):
    return r.get("sold_per_day")
