"""Tests: python test_app.py"""
import io
import os
import sys
import tempfile
import threading
import zipfile
from datetime import date, timedelta

os.environ["LAGER_DATA_DIR"] = tempfile.mkdtemp()

import app as lager  # noqa: E402
import db  # noqa: E402

client = lager.app.test_client()
TODAY = date.today()


def row(sql, *args):
    c = db.connect(lager.DB_PATH)
    r = c.execute(sql, args).fetchone()
    c.close()
    return r


def stock(aid):
    return row("SELECT stock FROM articles WHERE id = ?", aid)[0]


def lots_sum(aid):
    return round(row("SELECT COALESCE(SUM(qty),0) FROM lots WHERE article_id = ?", aid)[0], 3)


def cat_id(prefix):
    return row("SELECT id FROM categories WHERE prefix = ?", prefix)[0]


def new_article(name="Innenfarbe weiß", cat="WF", stock="0", min_stock="1", unit="Eimer", price="45,90",
                expires="", sources=None):
    sources = sources or [("Brillux", "Superlux 3000 weiß", "3000.0012.5", "", price)]
    data = {"art_nr": "", "name": name, "category_id": str(cat_id(cat)), "stock": stock, "min_stock": min_stock,
            "unit": unit, "color": "RAL 9010", "pack_size": "12,5 l", "expires": expires, "lot_nr": "",
            "src_preferred": "0", "src_id": [], "src_mfr": [], "src_name": [], "src_nr": [], "src_supplier": [],
            "src_price": []}
    for mfr, nm, nr, sup, pr in sources:
        for k, v in zip(("src_id", "src_mfr", "src_name", "src_nr", "src_supplier", "src_price"),
                        ("", mfr, nm, nr, sup, pr)):
            data[k].append(v)
    r = client.post("/artikel/neu", data=data)
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def book(aid, mtype, qty, **kw):
    data = {"type": mtype, "article_id": aid, "qty": qty, "user": "Anna"}
    data.update(kw)
    return client.post("/buchen", data=data, follow_redirects=True).get_data(as_text=True)


def test_decimal_quantities_and_numbers():
    aid = new_article(stock="3")
    assert row("SELECT art_nr FROM articles WHERE id = ?", aid)[0] == "WF-0001"
    assert stock(aid) == 3
    book(aid, "OUT", "0,5", site="Müller, Hauptstr. 5")
    book(aid, "OUT", "½", site="Müller, Hauptstr. 5")
    assert stock(aid) == 2
    book(aid, "RET", "0,25", site="Müller, Hauptstr. 5")
    assert stock(aid) == 2.25 and lots_sum(aid) == 2.25
    html = book(aid, "OUT", "5")
    assert "Nicht genug auf Lager" in html and stock(aid) == 2.25
    book(aid, "CORR", "2,5")
    assert stock(aid) == 2.5 and lots_sum(aid) == 2.5
    # Kosten der Baustelle: (0,5 + 0,5 - 0,25) * 45,90
    html = client.get("/baustelle?name=Müller, Hauptstr. 5").get_data(as_text=True)
    assert lager.euro(0.75 * 45.90) in html


def test_expiry_fefo_and_waste():
    old = (TODAY - timedelta(days=3)).isoformat()
    soon = (TODAY + timedelta(days=10)).isoformat()
    later = (TODAY + timedelta(days=300)).isoformat()
    aid = new_article(name="Tiefgrund LF", cat="GR", unit="Kanister", stock="1", expires=later)
    src = row("SELECT id FROM article_sources WHERE article_id = ?", aid)[0]
    book(aid, "IN", "2", source_id=src, expires=old, lot_nr="CH-OLD", unit_price="30,00")
    book(aid, "IN", "1", source_id=src, expires=soon)
    assert stock(aid) == 4
    assert row("SELECT last_price FROM articles WHERE id = ?", aid)[0] == 30.0
    # Meldungen: abgelaufene + bald ablaufende Charge
    html = client.get("/haltbarkeit").get_data(as_text=True)
    assert "CH-OLD" in html and "Entsorgen" in html
    # Entnahme nimmt zuerst die abgelaufene Charge und warnt
    html = book(aid, "OUT", "0,5", site="Schulze")
    assert "abgelaufen" in html
    assert row("SELECT qty FROM lots WHERE lot_nr = 'CH-OLD'")[0] == 1.5
    # Rückgabe geht in dieselbe Charge zurück
    book(aid, "RET", "0,5", site="Schulze")
    assert row("SELECT qty FROM lots WHERE lot_nr = 'CH-OLD'")[0] == 2.0
    # Gezielt die abgelaufene Charge entsorgen
    lot = row("SELECT id FROM lots WHERE lot_nr = 'CH-OLD'")[0]
    book(aid, "WASTE", "2", lot_id=lot, note="abgelaufen")
    assert row("SELECT qty FROM lots WHERE id = ?", lot)[0] == 0
    assert stock(aid) == 2 and lots_sum(aid) == 2
    assert "CH-OLD" not in client.get("/haltbarkeit").get_data(as_text=True)
    # Ablaufdatum nachträglich ändern
    lot2 = row("SELECT id FROM lots WHERE article_id = ? AND expires = ?", aid, soon)[0]
    client.post(f"/charge/{lot2}", data={"expires": later, "lot_nr": "X1"})
    assert row("SELECT expires, lot_nr FROM lots WHERE id = ?", lot2)[:] == (later, "X1")


def test_devices():
    r = client.post("/geraete/neu", data={"inv_nr": "", "name": "Airless Wagner", "category": "Spritzgeräte / Airless",
                                          "state": "ok", "price": "1.299,00"})
    did = int(r.headers["Location"].split("/")[-1])
    assert row("SELECT inv_nr, price FROM devices WHERE id = ?", did)[:] == ("G-0001", 1299.0)
    due = (TODAY - timedelta(days=1)).isoformat()
    client.post(f"/geraete/{did}/ausgeben", data={"person": "Tom", "site": "Schulze", "due_at": due, "user": "Anna"})
    html = client.get("/").get_data(as_text=True)
    assert "überfällig" in html.lower() or "Geräte überfällig" in html
    assert "Airless Wagner" in client.get("/baustelle?name=Schulze").get_data(as_text=True)
    # doppelt ausgeben geht nicht
    html = client.post(f"/geraete/{did}/ausgeben", data={"person": "Max", "user": "Anna"},
                       follow_redirects=True).get_data(as_text=True)
    assert "bereits ausgegeben" in html
    client.post(f"/geraete/{did}/zurueck", data={"state": "defekt", "back_note": "Düse verstopft"})
    assert row("SELECT back_at IS NOT NULL FROM loans WHERE device_id = ?", did)[0] == 1
    assert row("SELECT state FROM devices WHERE id = ?", did)[0] == "defekt"
    assert "defekt" in client.get("/geraete?filter=defekt").get_data(as_text=True)


def test_pages_and_export():
    aid = new_article(name="Abdeckfolie", cat="AB", unit="Rolle", stock="0")
    for url in ["/", "/artikel", "/artikel?filter=wenig", "/artikel?q=RAL", f"/artikel/{aid}",
                f"/artikel/{aid}/bearbeiten", "/artikel/neu", "/artikel/neu?kategorie=1", "/buchen?type=OUT",
                "/buchen?type=OUT&suche=farbe", f"/buchen?type=IN&artikel={aid}", f"/buchen?type=RET&artikel={aid}",
                f"/buchen?type=WASTE&artikel={aid}", f"/buchen?type=CORR&artikel={aid}", "/bewegungen",
                "/bewegungen?export=1", "/bestellliste", "/bestellliste?export=1", "/haltbarkeit",
                "/haltbarkeit?alle=1", "/baustellen", "/baustelle?name=Schulze&export=1", "/geraete", "/geraete/1",
                "/geraete/neu", "/geraete/1/bearbeiten", "/analyse", "/analyse?zeitraum=woche",
                "/analyse?zeitraum=jahr", "/stammdaten", "/export/artikel.csv", "/api/naechste-nummer?kategorie=1"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code)
    assert "Abdeckfolie" in client.get("/bestellliste").get_data(as_text=True)
    zf = zipfile.ZipFile(io.BytesIO(client.get("/export/alle-daten.zip").data))
    assert {"artikel.csv", "bewegungen.csv", "chargen_haltbarkeit.csv", "geraete.csv",
            "geraete_ausgaben.csv"} <= set(zf.namelist())
    assert "0,5" in zf.read("bewegungen.csv").decode("utf-8-sig")
    assert client.get("/api/ping").get_json() == {"app": "dillge-lager"}


def test_concurrent_bookings():
    aid = new_article(name="Kreppband", cat="AB", unit="Rolle", stock="0", min_stock="0")

    def worker():
        c = db.connect(lager.DB_PATH)
        for _ in range(20):
            db.book(c, aid, "IN", 0.5, user="t")
        c.close()
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert stock(aid) == 40 and lots_sum(aid) == 40


def test_fmt():
    assert db.fmt(0.5) == "0,5" and db.fmt(2) == "2" and db.fmt(1250.25) == "1.250,25"
    assert lager.to_float("1.250,5") == 1250.5 and lager.to_float("¾") == 0.75 and lager.to_float("2.5") == 2.5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    sys.exit(0)
