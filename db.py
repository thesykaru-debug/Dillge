"""Datenbank-Schicht (SQLite) für das Dillge-Lager (Malerbetrieb).

Bestand wird in Chargen (Tabelle lots) geführt: jeder Zugang ist eine Charge mit
optionalem Ablaufdatum/Chargennummer. Entnahmen verbrauchen zuerst die Charge,
die am frühesten abläuft. So kann das Programm melden, was abgelaufen ist.
"""
import os
import sqlite3
import sys
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE,
    sort    INTEGER NOT NULL DEFAULT 0,
    prefix  TEXT
);

CREATE TABLE IF NOT EXISTS manufacturers (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY,
    art_nr          TEXT NOT NULL UNIQUE,          -- unsere Artikelnummer
    name            TEXT NOT NULL,
    category_id     INTEGER REFERENCES categories(id),
    color           TEXT,                          -- Farbton (RAL/NCS/…)
    pack_size       TEXT,                          -- Gebinde, z. B. "12,5 l"
    description     TEXT,
    unit            TEXT NOT NULL DEFAULT 'Stück', -- Eimer, Liter, kg, Rolle …
    stock           REAL NOT NULL DEFAULT 0,
    min_stock       REAL NOT NULL DEFAULT 0,       -- Meldebestand
    location        TEXT,                          -- Lagerort
    last_price      REAL,                          -- letzter Einkaufspreis je Einheit
    photo           TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Bezugsquellen: mehrere Hersteller/Lieferanten je Artikel, jeder mit eigener Bezeichnung
CREATE TABLE IF NOT EXISTS article_sources (
    id              INTEGER PRIMARY KEY,
    article_id      INTEGER NOT NULL REFERENCES articles(id),
    manufacturer_id INTEGER REFERENCES manufacturers(id),
    supplier        TEXT,
    mfr_art_nr      TEXT,
    designation     TEXT,
    last_price      REAL,
    last_price_at   TEXT,
    preferred       INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    sort            INTEGER NOT NULL DEFAULT 0
);

-- Chargen: Teilmengen des Bestands mit Ablaufdatum/Chargennummer
CREATE TABLE IF NOT EXISTS lots (
    id          INTEGER PRIMARY KEY,
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    lot_nr      TEXT,
    expires     TEXT,                              -- JJJJ-MM-TT oder NULL
    qty         REAL NOT NULL DEFAULT 0,           -- noch vorhanden
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movements (
    id          INTEGER PRIMARY KEY,
    article_id  INTEGER NOT NULL REFERENCES articles(id),
    type        TEXT NOT NULL CHECK (type IN ('IN', 'OUT', 'RET', 'WASTE', 'CORR')),
    qty         REAL NOT NULL,                     -- Veränderung (+/-)
    stock_after REAL NOT NULL,
    unit_price  REAL,
    user        TEXT,
    site        TEXT,                              -- Baustelle / Kunde / Auftrag
    note        TEXT,
    source_id   INTEGER REFERENCES article_sources(id),
    created_at  TEXT NOT NULL
);

-- Welche Charge wurde durch welche Buchung wie stark verändert
CREATE TABLE IF NOT EXISTS lot_moves (
    movement_id INTEGER NOT NULL REFERENCES movements(id),
    lot_id      INTEGER NOT NULL REFERENCES lots(id),
    qty         REAL NOT NULL
);

-- Geräte & Werkzeuge, die ausgegeben und zurückgebracht werden
CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY,
    inv_nr        TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    category      TEXT,
    manufacturer  TEXT,
    serial_nr     TEXT,
    purchase_date TEXT,
    price         REAL,
    location      TEXT,                            -- Stammplatz im Lager
    state         TEXT NOT NULL DEFAULT 'ok',      -- ok / defekt / reparatur
    photo         TEXT,
    note          TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS loans (
    id          INTEGER PRIMARY KEY,
    device_id   INTEGER NOT NULL REFERENCES devices(id),
    person      TEXT NOT NULL,                     -- wer hat es
    site        TEXT,                              -- Baustelle
    out_at      TEXT NOT NULL,
    due_at      TEXT,                              -- geplant zurück (optional)
    back_at     TEXT,
    out_user    TEXT,
    back_user   TEXT,
    note        TEXT,
    back_note   TEXT
);

CREATE INDEX IF NOT EXISTS idx_mov_article ON movements(article_id);
CREATE INDEX IF NOT EXISTS idx_mov_created ON movements(created_at);
CREATE INDEX IF NOT EXISTS idx_mov_site ON movements(site);
CREATE INDEX IF NOT EXISTS idx_art_category ON articles(category_id);
CREATE INDEX IF NOT EXISTS idx_src_article ON article_sources(article_id);
CREATE INDEX IF NOT EXISTS idx_lot_article ON lots(article_id);
CREATE INDEX IF NOT EXISTS idx_lotmove_mov ON lot_moves(movement_id);
CREATE INDEX IF NOT EXISTS idx_loan_device ON loans(device_id);
"""

DEFAULT_CATEGORIES = [  # (Name, Kürzel für Artikelnummern)
    ("Wandfarben / Dispersion", "WF"),
    ("Lacke", "LA"),
    ("Lasuren / Holzschutz", "LS"),
    ("Grundierungen / Tiefgrund", "GR"),
    ("Spachtel / Putze", "SP"),
    ("Tapeten / Vlies", "TA"),
    ("Kleber", "KL"),
    ("Abdeckmaterial (Folie, Band, Vlies)", "AB"),
    ("Silikon / Acryl", "SI"),
    ("Pinsel / Rollen / Kleinwerkzeug", "PR"),
    ("Schleifmittel", "SC"),
    ("Sonstiges", "SO"),
]

DEFAULT_UNITS = ["Eimer", "Dose", "Liter", "kg", "Sack", "Rolle", "Karton", "Kartusche", "Packung", "Stück", "Meter"]

EXPIRY_WARN_DAYS = 30  # „läuft bald ab“ ab so vielen Tagen vorher

# Liegt die Datenbank auf einem Netzlaufwerk, darf SQLite nicht im WAL-Modus laufen.
# Als fertiges Programm (.exe / Mac) ist das immer der Fall.
NETWORK_MODE = os.environ.get("LAGER_NETZLAUFWERK", "1" if getattr(sys, "frozen", False) else "0") == "1"

EPS = 1e-6


def r3(x):
    """Mengen auf 3 Nachkommastellen runden (gegen Rundungsfehler bei 0,1 + 0,2 …)."""
    return round(x + 0.0, 3)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def init_db(path):
    con = connect(path)
    con.execute("PRAGMA journal_mode = " + ("DELETE" if NETWORK_MODE else "WAL"))
    con.executescript(SCHEMA)
    if con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        for i, (name, prefix) in enumerate(DEFAULT_CATEGORIES):
            con.execute("INSERT INTO categories(name, sort, prefix) VALUES (?, ?, ?)", (name, i, prefix))
    con.close()


def next_number(con, table, column, prefix):
    """Nächste freie Nummer, z. B. WF-0001, WF-0002 … bzw. G-0001 für Geräte."""
    if not prefix:
        return ""
    prefix = prefix.strip().upper() + "-"
    highest = 0
    for (nr,) in con.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",)):
        tail = nr[len(prefix):]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:04d}"


def next_art_nr(con, category_id):
    row = con.execute("SELECT prefix FROM categories WHERE id = ?", (category_id,)).fetchone()
    return next_number(con, "articles", "art_nr", row["prefix"] if row else None)


def next_inv_nr(con):
    return next_number(con, "devices", "inv_nr", "G")


# ------------------------------------------------------------------ Backup ---

def backup(db_path, backup_dir, keep=30):
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, "lager_" + datetime.now().strftime("%Y-%m-%d_%H%M%S") + ".db")
    src = connect(db_path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    files = sorted(f for f in os.listdir(backup_dir) if f.startswith("lager_") and f.endswith(".db"))
    for old in files[:-keep]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass
    return target


def daily_backup(db_path, backup_dir, keep=30):
    os.makedirs(backup_dir, exist_ok=True)
    today = "lager_" + datetime.now().strftime("%Y-%m-%d")
    if not any(f.startswith(today) for f in os.listdir(backup_dir)):
        return backup(db_path, backup_dir, keep)
    return None


# --------------------------------------------------------------- Buchungen ---

TYPES_OUT = ("OUT", "WASTE")      # verringern den Bestand


def _consume(con, article_id, amount, lot_id=None):
    """amount aus den Chargen nehmen: zuerst die gewünschte Charge, dann die, die am
    frühesten abläuft (ohne Ablaufdatum zuletzt). Gibt [(lot_id, menge)] zurück."""
    used = []
    lots = con.execute("""SELECT id, qty FROM lots WHERE article_id = ? AND qty > 0
                          ORDER BY (id = ?) DESC, expires IS NULL, expires, id""",
                       (article_id, lot_id or 0)).fetchall()
    rest = amount
    for lot in lots:
        if rest <= EPS:
            break
        take = min(lot["qty"], rest)
        con.execute("UPDATE lots SET qty = ? WHERE id = ?", (r3(lot["qty"] - take), lot["id"]))
        used.append((lot["id"], take))
        rest = r3(rest - take)
    if rest > EPS:  # Bestand ohne Charge (sollte nicht vorkommen) – trotzdem sauber verbuchen
        lid = con.execute("INSERT INTO lots(article_id, qty, created_at) VALUES (?, 0, ?)",
                          (article_id, now())).lastrowid
        used.append((lid, rest))
    return used


def _add(con, article_id, amount, lot_nr=None, expires=None, into_lot=None):
    if into_lot:
        con.execute("UPDATE lots SET qty = qty + ? WHERE id = ?", (amount, into_lot))
        return into_lot
    return con.execute("INSERT INTO lots(article_id, lot_nr, expires, qty, created_at) VALUES (?,?,?,?,?)",
                       (article_id, lot_nr, expires, amount, now())).lastrowid


def book(con, article_id, mtype, qty, user=None, site=None, note=None, unit_price=None,
         source_id=None, lot_nr=None, expires=None, lot_id=None):
    """Lagerbewegung buchen (in einer exklusiven Transaktion – sicher bei mehreren PCs).

    mtype: 'IN' Wareneingang, 'OUT' Entnahme, 'RET' Rückgabe von der Baustelle,
           'WASTE' Entsorgung/Verlust, 'CORR' Inventur (qty = gezählter Bestand)
    qty:   Menge > 0 (bei CORR: neuer Bestand >= 0), Kommazahlen erlaubt
    lot_id: bei OUT/WASTE zuerst diese Charge verwenden (z. B. abgelaufene entsorgen)
    Gibt (ok, meldung) zurück.
    """
    qty = r3(qty)
    con.execute("BEGIN IMMEDIATE")
    try:
        art = con.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if art is None:
            con.execute("ROLLBACK")
            return False, "Artikel nicht gefunden."
        stock = r3(art["stock"])
        unit = art["unit"]
        price = art["last_price"]
        if mtype == "IN":
            delta = qty
            if source_id:
                src = con.execute("SELECT last_price FROM article_sources WHERE id = ? AND article_id = ?",
                                  (source_id, article_id)).fetchone()
                if src is None:
                    source_id = None
                elif unit_price is None and src["last_price"] is not None:
                    price = src["last_price"]
            if unit_price is not None:
                price = unit_price
        elif mtype in TYPES_OUT:
            if qty > stock + EPS:
                con.execute("ROLLBACK")
                return False, f"Nicht genug auf Lager (Bestand: {fmt(stock)} {unit})."
            delta = -qty
        elif mtype == "RET":
            delta = qty
        elif mtype == "CORR":
            delta = r3(qty - stock)
            if abs(delta) <= EPS:
                con.execute("ROLLBACK")
                return False, "Bestand ist bereits korrekt – nichts gebucht."
        else:
            con.execute("ROLLBACK")
            return False, "Unbekannte Buchungsart."

        new_stock = r3(stock + delta)
        ts = now()
        mid = con.execute(
            "INSERT INTO movements(article_id, type, qty, stock_after, unit_price, user, site, note, source_id,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (article_id, mtype, delta, new_stock, price, user, site, note,
             source_id if mtype == "IN" else None, ts)).lastrowid

        # Chargen fortschreiben
        if delta < 0:
            changes = [(lid, -q) for lid, q in _consume(con, article_id, -delta, lot_id)]
        elif mtype == "RET":
            # Rückgabe in die Charge, aus der zuletzt entnommen wurde (Ablaufdatum bleibt erhalten)
            last = con.execute("""SELECT lm.lot_id FROM lot_moves lm JOIN movements m ON m.id = lm.movement_id
                                  WHERE m.article_id = ? AND m.type = 'OUT' ORDER BY m.id DESC, lm.rowid DESC
                                  LIMIT 1""", (article_id,)).fetchone()
            changes = [(_add(con, article_id, delta, into_lot=last["lot_id"] if last else None), delta)]
        else:
            changes = [(_add(con, article_id, delta, lot_nr or None, expires or None), delta)]
        con.executemany("INSERT INTO lot_moves(movement_id, lot_id, qty) VALUES (?,?,?)",
                        [(mid, lid, r3(q)) for lid, q in changes])

        if mtype == "IN" and price is not None:
            con.execute("UPDATE articles SET stock = ?, last_price = ?, updated_at = ? WHERE id = ?",
                        (new_stock, price, ts, article_id))
            if source_id:
                con.execute("UPDATE article_sources SET last_price = ?, last_price_at = ? WHERE id = ?",
                            (price, ts, source_id))
        else:
            con.execute("UPDATE articles SET stock = ?, updated_at = ? WHERE id = ?", (new_stock, ts, article_id))
        con.execute("COMMIT")
        return True, f"Gebucht. Neuer Bestand: {fmt(new_stock)} {unit}."
    except Exception:
        con.execute("ROLLBACK")
        raise


def fmt(x):
    """Menge deutsch formatieren: 2 / 0,5 / 12,25 / 1.250"""
    if x is None:
        return "–"
    s = f"{r3(x):,.3f}".rstrip("0").rstrip(".")
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def expiry_state(expires, today=None):
    """'abgelaufen', 'bald' oder None"""
    if not expires:
        return None
    today = today or date.today()
    try:
        d = date.fromisoformat(expires)
    except ValueError:
        return None
    if d < today:
        return "abgelaufen"
    if (d - today).days <= EXPIRY_WARN_DAYS:
        return "bald"
    return None
