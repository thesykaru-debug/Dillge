"""Füllt eine LEERE Datenbank mit Beispieldaten zum Ausprobieren:  python demo_daten.py"""
import os
import random
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("LAGER_DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
import app as lager  # noqa: E402
import db  # noqa: E402

ARTICLES = [  # Nr-Kürzel, Bezeichnung, Farbton, Gebinde, Einheit, Hersteller, Herst.-Bezeichnung, Nr, Preis, Min, Verbrauch/Woche, haltbar(Monate)
    ("WF", "Innenfarbe weiß matt", "RAL 9010", "12,5 l", "Eimer", "Brillux", "Superlux 3000", "3000.0012.5", 89.90, 4, 3, 24),
    ("WF", "Innenfarbe Premium", "weiß", "12,5 l", "Eimer", "Caparol", "Indeko-plus", "CAP-948211", 104.50, 2, 1.5, 24),
    ("WF", "Fassadenfarbe Silikonharz", "NCS S 1005-Y20R", "12,5 l", "Eimer", "Caparol", "AmphiSilan", "CAP-904377", 159.00, 1, 0.5, 18),
    ("LA", "Weißlack seidenmatt", "RAL 9016", "2,5 l", "Dose", "Brillux", "Lacryl-PU 2,5l", "2087.0002.5", 61.20, 3, 1, 36),
    ("LA", "Heizkörperlack", "weiß", "0,75 l", "Dose", "Remmers", "Heizkörperlack", "R-3340", 24.80, 2, 0.5, 36),
    ("LS", "Holzlasur Eiche", "eiche hell", "2,5 l", "Dose", "Remmers", "HK-Lasur", "R-2250", 54.30, 1, 0.3, 36),
    ("GR", "Tiefgrund LF", "farblos", "10 l", "Kanister", "Caparol", "Dupa-grund", "CAP-904502", 48.90, 2, 1, 12),
    ("SP", "Feinspachtel", "weiß", "25 kg", "Sack", "Knauf", "Uniflott", "KN-4006", 39.50, 2, 1, 9),
    ("TA", "Malervlies glatt", "weiß", "25 m", "Rolle", "Erfurt", "Vlies 130", "ERF-130", 42.00, 4, 2, 0),
    ("KL", "Vlieskleister", "", "16 kg", "Eimer", "Metylan", "Direct", "MET-16", 57.00, 1, 0.5, 12),
    ("AB", "Abdeckfolie 4x5 m", "", "Rolle", "Rolle", "Storch", "Abdeckfolie", "ST-4810", 3.20, 20, 15, 0),
    ("AB", "Kreppband 38 mm", "", "50 m", "Rolle", "tesa", "tesakrepp", "tesa-4323", 2.90, 30, 20, 0),
    ("SI", "Maleracryl weiß", "weiß", "310 ml", "Kartusche", "Bostik", "Acryl", "BOS-310", 3.40, 15, 8, 12),
    ("PR", "Farbroller 25 cm", "", "Stück", "Stück", "Storch", "Profi-Roller", "ST-1825", 12.90, 6, 3, 0),
    ("SC", "Schleifpapier K120", "", "50 Blatt", "Packung", "Mirka", "Gold K120", "MIR-120", 19.80, 2, 1, 0),
]
SITES = ["Müller, Hauptstr. 5", "Schulze GmbH Büro", "Kita Sonnenschein", "Fam. Yilmaz, Gartenweg 12",
         "Praxis Dr. Weber", "Neubau Lindenallee"]
USERS = ["Anna", "Tom", "Murat", "Jens"]
DEVICES = [("Airless-Spritzgerät", "Spritzgeräte / Airless", "Wagner"), ("Langhalsschleifer", "Schleifmaschinen", "Festool"),
           ("Bockleiter 3 m", "Leitern / Gerüste", "Hymer"), ("Rollgerüst", "Leitern / Gerüste", "Layher"),
           ("Bautrockner", "Trockner / Heizer", "Trotec"), ("Exzenterschleifer", "Schleifmaschinen", "Festool")]


def main():
    c = db.connect(lager.DB_PATH)
    if c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]:
        sys.exit("Datenbank enthält bereits Artikel – Demo-Daten werden NICHT eingespielt.")
    random.seed(7)
    cats = {r["prefix"]: r["id"] for r in c.execute("SELECT id, prefix FROM categories")}
    start = datetime.now() - timedelta(days=330)
    for i, (pre, name, color, pack, unit, mfr, mname, mnr, price, mn, per_week, shelf) in enumerate(ARTICLES):
        ts = start.strftime("%Y-%m-%d %H:%M:%S")
        aid = c.execute("INSERT INTO articles(art_nr,name,category_id,color,pack_size,unit,min_stock,last_price,"
                        "location,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (db.next_art_nr(c, cats[pre]), name, cats[pre], color or None, pack, unit, mn, price,
                         f"Regal {i // 4 + 1}", ts, ts)).lastrowid
        c.execute("INSERT OR IGNORE INTO manufacturers(name) VALUES (?)", (mfr,))
        mid = c.execute("SELECT id FROM manufacturers WHERE name=?", (mfr,)).fetchone()[0]
        src = c.execute("INSERT INTO article_sources(article_id,manufacturer_id,designation,mfr_art_nr,supplier,"
                        "last_price,preferred) VALUES (?,?,?,?,?,?,1)",
                        (aid, mid, mname, mnr, "MEGA eG", price)).lastrowid
        t, p = start, price * 0.93
        while t < datetime.now() - timedelta(days=1):
            t += timedelta(days=random.randint(2, 7), hours=random.randint(1, 8))
            if t > datetime.now() - timedelta(hours=8):
                break
            stock = c.execute("SELECT stock FROM articles WHERE id=?", (aid,)).fetchone()[0]
            if stock < max(mn, per_week) * 1.2:
                p *= random.uniform(1.0, 1.02)
                exp = (t.date() + timedelta(days=30 * shelf)).isoformat() if shelf else None
                at(c, t, db.book(c, aid, "IN", max(mn, per_week) * 3, user=random.choice(USERS),
                                 unit_price=round(p, 2), source_id=src, expires=exp,
                                 lot_nr=f"CH{random.randint(1000, 9999)}" if shelf else None))
            q = round(random.choice([0.25, 0.5, 1, 1, 2]) * max(per_week / 2, 0.5) * 2) / 2 or 0.5
            site = random.choice(SITES)
            at(c, t, db.book(c, aid, "OUT", min(q, stock or q), user=random.choice(USERS), site=site))
            if unit in ("Eimer", "Dose", "Kanister") and random.random() < 0.25:
                at(c, t + timedelta(hours=6), db.book(c, aid, "RET", 0.5, user=random.choice(USERS), site=site))
    # eine abgelaufene Charge und eine, die bald abläuft
    c.execute("UPDATE lots SET expires = ? WHERE id = (SELECT MAX(id) FROM lots WHERE article_id = 7 AND qty > 0)",
              ((date.today() - timedelta(days=12)).isoformat(),))
    c.execute("UPDATE lots SET expires = ? WHERE id = (SELECT MAX(id) FROM lots WHERE article_id = 8 AND qty > 0)",
              ((date.today() + timedelta(days=18)).isoformat(),))
    c.execute("UPDATE articles SET stock = 0.5 WHERE id = 3")
    c.execute("UPDATE lots SET qty = 0 WHERE article_id = 3")
    c.execute("UPDATE lots SET qty = 0.5 WHERE id = (SELECT MAX(id) FROM lots WHERE article_id = 3)")
    for i, (name, cat, mfr) in enumerate(DEVICES):
        ts = db.now()
        did = c.execute("INSERT INTO devices(inv_nr,name,category,manufacturer,state,location,created_at,updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?)", (db.next_inv_nr(c), name, cat, mfr,
                                                      "reparatur" if i == 5 else "ok", "Halle", ts, ts)).lastrowid
        if i < 3:
            out = datetime.now() - timedelta(days=random.randint(2, 20))
            due = (date.today() + timedelta(days=-2 if i == 0 else 7)).isoformat()
            c.execute("INSERT INTO loans(device_id,person,site,out_at,due_at,out_user) VALUES (?,?,?,?,?,?)",
                      (did, USERS[i + 1], SITES[i], out.strftime("%Y-%m-%d %H:%M:%S"), due, "Anna"))
    print(f"Demo-Daten angelegt in {lager.DB_PATH}")


def at(c, t, result):
    """Zeitstempel der gerade gebuchten Bewegung in die Vergangenheit setzen."""
    if result[0]:
        c.execute("UPDATE movements SET created_at = ? WHERE id = (SELECT MAX(id) FROM movements)",
                  (t.strftime("%Y-%m-%d %H:%M:%S"),))


if __name__ == "__main__":
    main()
