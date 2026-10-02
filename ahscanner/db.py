"""SQLite-Speicher für AH-Snapshots (aggregiert pro Item)."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ts INTEGER NOT NULL,
    interval_h REAL,
    UNIQUE(source, ts)
);
CREATE TABLE IF NOT EXISTS item_stats (
    snapshot_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    total_qty INTEGER,
    n_auctions INTEGER,
    min_price INTEGER,
    market_price INTEGER,
    median_price INTEGER,
    sold_est REAL,
    PRIMARY KEY (snapshot_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_stats_item ON item_stats(item_id);
"""


SCHEMA_VERSION = 2


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            # Verkaufsschätzungen aus älteren Versionen waren zu hoch -> verwerfen, Preise behalten
            self.conn.execute("UPDATE item_stats SET sold_est=NULL")
            self.conn.execute("UPDATE snapshots SET interval_h=NULL")
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()

    def close(self):
        self.conn.close()

    def has_snapshot(self, source, ts):
        row = self.conn.execute("SELECT 1 FROM snapshots WHERE source=? AND ts=?", (source, ts)).fetchone()
        return row is not None

    def last_snapshot(self, source):
        return self.conn.execute(
            "SELECT id, ts FROM snapshots WHERE source=? ORDER BY ts DESC LIMIT 1", (source,)
        ).fetchone()

    def insert_snapshot(self, source, ts, interval_h, rows, retention_days=30):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO snapshots(source, ts, interval_h) VALUES (?,?,?)", (source, ts, interval_h))
        sid = cur.lastrowid
        cur.executemany(
            "INSERT INTO item_stats(snapshot_id,item_id,total_qty,n_auctions,min_price,market_price,median_price,sold_est)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(sid, *row) for row in rows],
        )
        cutoff = int(time.time() - retention_days * 86400)
        old = [r[0] for r in cur.execute("SELECT id FROM snapshots WHERE ts < ?", (cutoff,))]
        if old:
            cur.executemany("DELETE FROM item_stats WHERE snapshot_id=?", [(i,) for i in old])
            cur.executemany("DELETE FROM snapshots WHERE id=?", [(i,) for i in old])
        self.conn.commit()
        if old:
            self.conn.execute("VACUUM")  # Platz wirklich freigeben
        return sid

    def snapshot_overview(self):
        return self.conn.execute(
            "SELECT source, COUNT(*), MIN(ts), MAX(ts), SUM(COALESCE(interval_h,0)) FROM snapshots GROUP BY source"
        ).fetchall()

    def latest_stats(self):
        """Aktueller Stand pro Item. Rohstoffe (commodity) haben Vorrang vor Realm-Daten."""
        result = {}
        for source in ("realm", "commodity"):  # commodity überschreibt realm
            last = self.last_snapshot(source)
            if not last:
                continue
            sid, ts = last
            for row in self.conn.execute(
                "SELECT item_id,total_qty,n_auctions,min_price,market_price,median_price FROM item_stats"
                " WHERE snapshot_id=? AND total_qty>0",
                (sid,),
            ):
                result[row[0]] = {
                    "source": source,
                    "ts": ts,
                    "total_qty": row[1],
                    "n_auctions": row[2],
                    "min_price": row[3],
                    "market_price": row[4],
                    "median_price": row[5],
                }
        return result

    def history(self, cutoff_ts):
        hours = {
            src: h or 0.0
            for src, h in self.conn.execute(
                "SELECT source, SUM(interval_h) FROM snapshots WHERE ts>=? AND interval_h IS NOT NULL GROUP BY source",
                (cutoff_ts,),
            )
        }
        sold = {}
        for src, item, total in self.conn.execute(
            "SELECT s.source, i.item_id, SUM(i.sold_est) FROM item_stats i JOIN snapshots s ON s.id=i.snapshot_id"
            " WHERE s.ts>=? AND s.interval_h IS NOT NULL GROUP BY s.source, i.item_id",
            (cutoff_ts,),
        ):
            sold[(src, item)] = total or 0.0
        avg = {}
        for src, item, a in self.conn.execute(
            "SELECT s.source, i.item_id, AVG(i.market_price) FROM item_stats i JOIN snapshots s ON s.id=i.snapshot_id"
            " WHERE s.ts>=? AND i.market_price IS NOT NULL GROUP BY s.source, i.item_id",
            (cutoff_ts,),
        ):
            avg[(src, item)] = a
        last_price = {}
        for item, price in self.conn.execute(
            "SELECT i.item_id, i.market_price FROM item_stats i JOIN snapshots s ON s.id=i.snapshot_id"
            " WHERE i.market_price IS NOT NULL ORDER BY s.ts ASC"
        ):
            last_price[item] = price
        return {"hours": hours, "sold": sold, "avg": avg, "last_price": last_price}
