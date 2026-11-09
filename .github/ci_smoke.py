"""Rauchtest gegen das laufende fertige Programm: Artikel anlegen, buchen, Gerät ausgeben."""
import http.cookiejar
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def get(path):
    with opener.open(BASE + path, timeout=15) as r:
        return r.read()


def post(path, data):
    with opener.open(BASE + path, urllib.parse.urlencode(data, doseq=True).encode(), timeout=15) as r:
        return r.read().decode("utf-8")


for _ in range(90):
    try:
        assert json.loads(get("/api/ping"))["app"] == "dillge-lager"
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit("Programm antwortet nicht")

post("/artikel/neu", {"art_nr": "", "name": "CI-Farbe", "category_id": "1", "stock": "3", "min_stock": "1",
                      "unit": "Eimer", "expires": "2020-01-01", "src_preferred": "0", "src_id": [""],
                      "src_mfr": ["Brillux"], "src_name": ["Superlux"], "src_nr": ["CI-1"], "src_supplier": [""],
                      "src_price": ["50,00"]})
assert "WF-0001" in get("/artikel?q=CI-Farbe").decode(), "Artikel nicht angelegt"
post("/buchen", {"type": "OUT", "article_id": "1", "qty": "0,5", "user": "CI", "site": "Testbaustelle"})
assert "<b>2,5</b>" in get("/artikel?q=CI-Farbe").decode(), "Buchung fehlgeschlagen"
assert "CI-Farbe" in get("/haltbarkeit").decode(), "Haltbarkeitsmeldung fehlt"
post("/geraete/neu", {"inv_nr": "", "name": "CI-Leiter", "state": "ok"})
post("/geraete/1/ausgeben", {"person": "Tom", "site": "Testbaustelle", "user": "CI"})
assert "CI-Leiter" in get("/baustelle?name=Testbaustelle").decode(), "Geräteausgabe fehlt"
for page in ["/", "/analyse?zeitraum=jahr", "/bestellliste", "/stammdaten", "/static/style.css",
             "/export/alle-daten.zip"]:
    get(page)
print("Rauchtest OK")
