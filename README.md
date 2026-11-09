# Dillge Lager

Lagerverwaltung für den Malerbetrieb: Material, Geräte und Baustellen. Ein fertiges Programm für
**Windows und Mac**, das in einem gemeinsamen Ordner auf der Netzwerkfestplatte liegt – alle Rechner arbeiten
mit denselben Daten.

## Funktionen

| Bereich | Was es kann |
|---|---|
| **Übersicht** | Lagerwert, Materialverbrauch und Einkauf im Monat, Meldungen: wenig/leer, abgelaufen/läuft bald ab, Geräte überfällig |
| **Material** | Artikelnr. automatisch passend zur Kategorie (z. B. `WF-0001`), Bezeichnung, Farbton (RAL/NCS), Gebinde, Einheit (Eimer, Dose, Liter, kg, Rolle …), Lagerort, Meldebestand, Foto |
| **Kommamengen** | Alles in halben/viertel Einheiten buchbar: „0,5 Eimer“, Schnellknöpfe ¼ ½ ¾ |
| **Bezugsquellen** | Mehrere Hersteller/Lieferanten je Artikel, jeder mit eigener Bezeichnung, Bestellnr. und Preis |
| **Buchen** | Entnahme (mit Baustelle, optional), **Rückgabe** von der Baustelle (z. B. ½ Eimer zurück), Wareneingang (mit Preis, Ablaufdatum, Charge), **Entsorgung**, Inventur |
| **Haltbarkeit** | Ablaufdatum je Charge; Meldung, wenn abgelaufen oder in 30 Tagen abläuft; Entnahmen verbrauchen automatisch zuerst die Ware, die am frühesten abläuft |
| **Geräte** | Leitern, Spritzgeräte, Maschinen …: ausgeben an Person/Baustelle, „zurück bis“-Datum mit Überfällig-Meldung, Rückgabe mit Zustand (defekt/Reparatur), kompletter Verlauf |
| **Baustellen** | Materialkosten je Baustelle/Kunde (Entnahmen minus Rückgaben), alle Buchungen, Geräte vor Ort |
| **Analyse** | Woche/Monat/Jahr: Verbrauch, Einkauf, Verlust durch Entsorgung, Kosten je Baustelle und Kategorie, Top 10, Ladenhüter |
| **Export** | „Alle Daten (CSV)“ – alles als CSV für Excel |

## Programm herunterladen

Bei jeder Änderung baut GitHub das Programm automatisch neu, testet es und legt es **dauerhaft** unter
**Releases → „Dillge Lager – aktuelle Version“** ab:

* `DillgeLager.exe` – Windows
* `DillgeLager-Mac.zip` – Mac mit Apple-Chip (M1–M4)
* `DillgeLager-Mac-Intel.zip` – Mac mit Intel-Prozessor
* `ANLEITUNG.txt` – Einrichten und Benutzen

## Aufbau

```
Netzwerkfestplatte\Lager\
   DillgeLager.exe            ← Windows-Rechner starten diese Datei
   DillgeLager-Mac            ← Macs starten diese Datei
   data\lager.db              ← gemeinsame Datenbank
   data\fotos\  data\backups\
```

Das Programm schreibt nur in den Ordner `data` daneben und ist nur am jeweiligen Rechner selbst erreichbar.

## Für Entwickler

```bash
pip install -r requirements.txt
python test_app.py           # Tests
python demo_daten.py         # leere Datenbank mit Beispieldaten füllen
python app.py                # starten: http://localhost:8080
pyinstaller lager.spec       # fertiges Programm bauen
```

Python 3 · Flask · Waitress · SQLite (auf Netzlaufwerken ohne WAL). Umgebungsvariablen: `LAGER_DATA_DIR`,
`LAGER_BACKUP_DIR`, `LAGER_PORT`, `LAGER_NETZLAUFWERK`, `LAGER_NO_BROWSER`.
