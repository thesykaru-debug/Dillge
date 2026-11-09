"""Dillge-Lager – Lagerverwaltung für den Malerbetrieb (Web-Anwendung).

Normalbetrieb: Das fertige Programm liegt im gemeinsamen Ordner auf der Netzwerkfestplatte,
jeder Rechner (Windows oder Mac) startet es, alle arbeiten mit denselben Daten im Ordner
"data" daneben. Siehe README.md.
"""
import csv
import io
import os
import sys
import uuid
import zipfile
from datetime import date, datetime, timedelta

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, url_for)

import db

if getattr(sys, "frozen", False):  # als fertiges Programm gestartet
    RESOURCE_DIR = sys._MEIPASS
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    RESOURCE_DIR = BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("LAGER_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "lager.db")
PHOTO_DIR = os.path.join(DATA_DIR, "fotos")
BACKUP_DIR = os.environ.get("LAGER_BACKUP_DIR", os.path.join(DATA_DIR, "backups"))
ALLOWED_PHOTO = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}

TYPE_LABELS = {"OUT": "Entnahme", "RET": "Rückgabe", "IN": "Eingang", "WASTE": "Entsorgung", "CORR": "Inventur"}
DEVICE_STATES = {"ok": "einsatzbereit", "defekt": "defekt", "reparatur": "in Reparatur"}
DEVICE_CATEGORIES = ["Leitern / Gerüste", "Spritzgeräte / Airless", "Schleifmaschinen", "Elektrowerkzeug",
                     "Trockner / Heizer", "Fahrzeugzubehör", "Sonstiges"]
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# Kosten = Entnahme minus Rückgabe (mit dem Preis zum Zeitpunkt der Buchung)
COST_SQL = "SUM(CASE WHEN mv.type IN ('OUT','RET') THEN -mv.qty * COALESCE(mv.unit_price,0) END)"
NET_QTY_SQL = "SUM(CASE WHEN mv.type IN ('OUT','RET') THEN -mv.qty END)"


def create_app():
    os.makedirs(PHOTO_DIR, exist_ok=True)
    db.init_db(DB_PATH)
    try:
        db.daily_backup(DB_PATH, BACKUP_DIR)
    except OSError as exc:
        print("WARNUNG: Backup fehlgeschlagen:", exc)
    app = Flask(__name__, template_folder=os.path.join(RESOURCE_DIR, "templates"),
                static_folder=os.path.join(RESOURCE_DIR, "static"))
    app.config["SECRET_KEY"] = _secret_key()
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
    register(app)
    return app


def _secret_key():
    path = os.path.join(DATA_DIR, ".secret")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(uuid.uuid4().hex)
    with open(path) as fh:
        return fh.read().strip()


# ---------------------------------------------------------------- Hilfen ---

def con():
    if "con" not in g:
        g.con = db.connect(DB_PATH)
    return g.con


def q(sql, args=(), one=False):
    cur = con().execute(sql, args)
    return cur.fetchone() if one else cur.fetchall()


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_float(value, default=None):
    """Deutsche Zahleneingabe: 0,5 / 1.250,75 / 12.50 / ½"""
    if value is None:
        return default
    value = str(value).strip().replace("€", "").replace(" ", "")
    value = value.replace("½", ".5").replace("¼", ".25").replace("¾", ".75")
    if not value:
        return default
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return default


def to_date(value):
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def euro(value):
    if value is None:
        return "–"
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def num(value):
    if value is None:
        return "–"
    return f"{value:,.0f}".replace(",", ".")


def dt(value):
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def d(value):
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return value


def de_num(value, digits=2):
    if value is None:
        return ""
    return f"{value:.{digits}f}".replace(".", ",")


def de_qty(value):
    return "" if value is None else db.fmt(value).replace(".", "")


def status_of(art):
    if art["stock"] <= db.EPS:
        return "leer"
    if art["stock"] <= art["min_stock"] + db.EPS:
        return "wenig"
    return "ok"


def current_user():
    return request.cookies.get("lager_user", "")


def remember_user(resp, name):
    if name:
        resp.set_cookie("lager_user", name, max_age=60 * 60 * 24 * 365)
    return resp


def save_photo(file):
    if not file or not file.filename:
        return None
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_PHOTO:
        flash("Foto: nur JPG, PNG, GIF, WEBP oder HEIC erlaubt.", "error")
        return None
    name = uuid.uuid4().hex + ext
    file.save(os.path.join(PHOTO_DIR, name))
    return name


ARTICLE_SELECT = """
    SELECT a.*, c.name AS category, m.name AS manufacturer,
           ps.mfr_art_nr AS src_nr, ps.designation AS src_name, ps.supplier AS src_supplier,
           (SELECT COUNT(*) FROM article_sources x WHERE x.article_id = a.id AND x.active = 1) AS n_sources,
           (SELECT MIN(expires) FROM lots l WHERE l.article_id = a.id AND l.qty > 0) AS next_expiry
    FROM articles a
    LEFT JOIN categories c ON c.id = a.category_id
    LEFT JOIN article_sources ps ON ps.article_id = a.id AND ps.preferred = 1 AND ps.active = 1
    LEFT JOIN manufacturers m ON m.id = ps.manufacturer_id
"""

SOURCES_SELECT = """
    SELECT s.*, m.name AS manufacturer FROM article_sources s
    LEFT JOIN manufacturers m ON m.id = s.manufacturer_id
    WHERE s.article_id = ? AND s.active = 1 ORDER BY s.preferred DESC, s.sort, s.id
"""

SOURCE_SEARCH = """EXISTS (SELECT 1 FROM article_sources s LEFT JOIN manufacturers sm ON sm.id = s.manufacturer_id
    WHERE s.article_id = a.id AND s.active = 1
      AND (s.mfr_art_nr LIKE ? OR s.designation LIKE ? OR s.supplier LIKE ? OR sm.name LIKE ?))"""

LOAN_OPEN = "(SELECT l.id FROM loans l WHERE l.device_id = dv.id AND l.back_at IS NULL ORDER BY l.id DESC LIMIT 1)"
DEVICE_SELECT = f"""
    SELECT dv.*, ln.id AS loan_id, ln.person, ln.site AS loan_site, ln.out_at, ln.due_at
    FROM devices dv LEFT JOIN loans ln ON ln.id = {LOAN_OPEN}
"""


def sources_of(aid):
    return q(SOURCES_SELECT, (aid,))


def sources_by_article(article_ids):
    out = {}
    ids = list(article_ids)
    if not ids:
        return out
    rows = q("SELECT s.*, m.name AS manufacturer FROM article_sources s"
             " LEFT JOIN manufacturers m ON m.id = s.manufacturer_id"
             f" WHERE s.active = 1 AND s.article_id IN ({','.join('?' * len(ids))})"
             " ORDER BY s.preferred DESC, s.sort, s.id", ids)
    for r in rows:
        out.setdefault(r["article_id"], []).append(r)
    return out


def low_stock_articles():
    return q(ARTICLE_SELECT + " WHERE a.active = 1 AND a.stock <= a.min_stock + 0.000001"
             " ORDER BY a.stock > 0.000001, c.sort, a.name")


def expiring_lots(only_warn=True):
    limit = (date.today() + timedelta(days=db.EXPIRY_WARN_DAYS)).isoformat()
    sql = """SELECT l.*, a.art_nr, a.name, a.unit, a.color, a.location, a.last_price, a.id AS article_id
             FROM lots l JOIN articles a ON a.id = l.article_id
             WHERE l.qty > 0.000001 AND l.expires IS NOT NULL AND a.active = 1"""
    if only_warn:
        return q(sql + " AND l.expires <= ? ORDER BY l.expires", (limit,))
    return q(sql + " ORDER BY l.expires")


def overdue_loans():
    return q(DEVICE_SELECT + " WHERE dv.active = 1 AND ln.id IS NOT NULL AND ln.due_at IS NOT NULL"
             " AND ln.due_at < ? ORDER BY ln.due_at", (date.today().isoformat(),))


def known_sites():
    return [r[0] for r in q("""SELECT site FROM (SELECT site, created_at FROM movements WHERE site IS NOT NULL
                               UNION ALL SELECT site, out_at FROM loans WHERE site IS NOT NULL)
                               GROUP BY site ORDER BY MAX(created_at) DESC LIMIT 300""")]


def known_people():
    return [r[0] for r in q("""SELECT p FROM (SELECT person AS p, out_at AS t FROM loans
                               UNION ALL SELECT user, created_at FROM movements WHERE user IS NOT NULL)
                               GROUP BY p ORDER BY MAX(t) DESC LIMIT 100""")]


def period_range(period, ref):
    if period == "woche":
        start = ref - timedelta(days=ref.weekday())
        end = start + timedelta(days=7)
        label = f"KW {start.isocalendar()[1]} / {start.isocalendar()[0]}"
        prev_ref, next_ref = start - timedelta(days=7), end
    elif period == "jahr":
        start, end = date(ref.year, 1, 1), date(ref.year + 1, 1, 1)
        label = str(ref.year)
        prev_ref, next_ref = date(ref.year - 1, 1, 1), end
    else:
        start = date(ref.year, ref.month, 1)
        end = date(ref.year + (ref.month == 12), ref.month % 12 + 1, 1)
        label = f"{MONTHS[ref.month - 1]} {ref.year}"
        prev_ref, next_ref = start - timedelta(days=1), end
    return start, end, label, prev_ref, next_ref


def csv_bytes(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM, damit Excel Umlaute richtig liest


def csv_response(filename, header, rows):
    return send_file(io.BytesIO(csv_bytes(header, rows)), mimetype="text/csv", as_attachment=True,
                     download_name=filename.replace(".csv", f"_{date.today().isoformat()}.csv"))


NO_SOURCE = {"preferred": 0, "manufacturer": None, "designation": None, "mfr_art_nr": None,
             "supplier": None, "last_price": None}


# ---------------------------------------------------------------- Routen ---

def register(app):
    app.jinja_env.filters.update(euro=euro, num=num, dt=dt, d=d, qty=db.fmt, qty_in=de_qty)
    app.jinja_env.globals.update(TYPE_LABELS=TYPE_LABELS, status_of=status_of, expiry_state=db.expiry_state,
                                 DEVICE_STATES=DEVICE_STATES, today=lambda: date.today().isoformat(),
                                 WARN_DAYS=db.EXPIRY_WARN_DAYS)

    @app.teardown_appcontext
    def close_db(_exc):
        c = g.pop("con", None)
        if c is not None:
            c.close()

    @app.context_processor
    def inject():
        if request.endpoint in (None, "static", "photo", "ping"):
            return {}
        low = q("SELECT COUNT(*) FROM articles WHERE active = 1 AND stock <= min_stock + 0.000001", one=True)[0]
        return {"low_count": low, "expiry_count": len(expiring_lots()), "user_name": current_user()}

    # ------------------------------------------------------------ Übersicht
    @app.route("/")
    def dashboard():
        stats = q("""SELECT COUNT(*) AS n, COALESCE(SUM(stock * COALESCE(last_price,0)),0) AS value
                     FROM articles WHERE active = 1""", one=True)
        month_start = date.today().replace(day=1).isoformat()
        month = q(f"""SELECT COALESCE({COST_SQL},0) AS cost,
                COALESCE(SUM(CASE WHEN mv.type='IN' THEN mv.qty * COALESCE(mv.unit_price,0) END),0) AS in_cost,
                COALESCE(SUM(CASE WHEN mv.type='WASTE' THEN -mv.qty * COALESCE(mv.unit_price,0) END),0) AS waste
                FROM movements mv WHERE mv.created_at >= ?""", (month_start,), one=True)
        devices_out = q("SELECT COUNT(*) FROM loans l JOIN devices dv ON dv.id = l.device_id"
                        " WHERE l.back_at IS NULL AND dv.active = 1", one=True)[0]
        recent = q("""SELECT mv.*, a.art_nr, a.name, a.unit FROM movements mv
                      JOIN articles a ON a.id = mv.article_id ORDER BY mv.created_at DESC, mv.id DESC LIMIT 12""")
        return render_template("dashboard.html", stats=stats, month=month, recent=recent,
                               low=low_stock_articles(), expiring=expiring_lots(), overdue=overdue_loans(),
                               devices_out=devices_out)

    # ------------------------------------------------------------ Artikel
    @app.route("/artikel")
    def articles():
        search = request.args.get("q", "").strip()
        cat = to_int(request.args.get("kategorie"))
        flt = request.args.get("filter", "")
        where, args = (["a.active = 0"] if flt == "inaktiv" else ["a.active = 1"]), []
        if search:
            like = f"%{search}%"
            where.append("(a.art_nr LIKE ? OR a.name LIKE ? OR a.color LIKE ? OR a.description LIKE ?"
                         " OR a.location LIKE ? OR " + SOURCE_SEARCH + ")")
            args += [like] * 9
        if cat:
            where.append("a.category_id = ?")
            args.append(cat)
        if flt == "wenig":
            where.append("a.stock <= a.min_stock + 0.000001")
        rows = q(ARTICLE_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY c.sort, a.art_nr", args)
        cats = q("SELECT * FROM categories ORDER BY sort, name")
        return render_template("articles.html", rows=rows, cats=cats, search=search, cat=cat, flt=flt)

    @app.route("/artikel/<int:aid>")
    def article(aid):
        art = q(ARTICLE_SELECT + " WHERE a.id = ?", (aid,), one=True) or abort(404)
        moves = q("""SELECT mv.*, m.name AS src_mfr, s.supplier AS src_supplier FROM movements mv
                     LEFT JOIN article_sources s ON s.id = mv.source_id
                     LEFT JOIN manufacturers m ON m.id = s.manufacturer_id
                     WHERE mv.article_id = ? ORDER BY mv.id DESC LIMIT 200""", (aid,))
        lots = q("SELECT * FROM lots WHERE article_id = ? AND qty > 0.000001 ORDER BY expires IS NULL, expires, id",
                 (aid,))
        year_start = date.today().replace(month=1, day=1).isoformat()
        usage = q(f"""SELECT COALESCE({NET_QTY_SQL},0) AS qty, COALESCE({COST_SQL},0) AS cost
                      FROM movements mv WHERE mv.article_id = ? AND mv.created_at >= ?""",
                  (aid, year_start), one=True)
        return render_template("article.html", art=art, moves=moves, usage=usage, lots=lots,
                               sources=sources_of(aid))

    def article_form_data():
        f = request.form
        return {
            "art_nr": f.get("art_nr", "").strip(),
            "name": f.get("name", "").strip(),
            "category_id": to_int(f.get("category_id")),
            "color": f.get("color", "").strip() or None,
            "pack_size": f.get("pack_size", "").strip() or None,
            "description": f.get("description", "").strip() or None,
            "unit": f.get("unit", "").strip() or "Stück",
            "min_stock": max(to_float(f.get("min_stock"), 0), 0),
            "location": f.get("location", "").strip() or None,
        }

    def form_sources():
        f = request.form
        rows = []
        cols = zip(f.getlist("src_id"), f.getlist("src_mfr"), f.getlist("src_supplier"),
                   f.getlist("src_nr"), f.getlist("src_name"), f.getlist("src_price"))
        pref = to_int(f.get("src_preferred"), 0)
        for i, (sid, mfr, sup, nr, name, price) in enumerate(cols):
            row = {"id": to_int(sid), "manufacturer": mfr.strip(), "supplier": sup.strip() or None,
                   "mfr_art_nr": nr.strip() or None, "designation": name.strip() or None,
                   "last_price": to_float(price), "preferred": int(i == pref), "sort": i}
            if row["manufacturer"] or row["supplier"] or row["mfr_art_nr"] or row["designation"] \
                    or row["last_price"] is not None:
                rows.append(row)
        if rows and not any(r["preferred"] for r in rows):
            rows[0]["preferred"] = 1
        return rows

    def manufacturer_id(name):
        name = (name or "").strip()
        if not name:
            return None
        row = q("SELECT id FROM manufacturers WHERE name = ? COLLATE NOCASE", (name,), one=True)
        if row:
            return row["id"]
        return con().execute("INSERT INTO manufacturers(name) VALUES (?)", (name,)).lastrowid

    def save_sources(aid, rows):
        keep = set()
        for r in rows:
            vals = (manufacturer_id(r["manufacturer"]), r["supplier"], r["mfr_art_nr"], r["designation"],
                    r["last_price"], r["preferred"], r["sort"])
            old = q("SELECT last_price FROM article_sources WHERE id = ? AND article_id = ?",
                    (r["id"], aid), one=True) if r["id"] else None
            if old:
                con().execute("""UPDATE article_sources SET manufacturer_id=?, supplier=?, mfr_art_nr=?,
                                 designation=?, last_price=?, preferred=?, sort=?, active=1 WHERE id=?""",
                              vals + (r["id"],))
                if old["last_price"] != r["last_price"]:
                    con().execute("UPDATE article_sources SET last_price_at = ? WHERE id = ?", (db.now(), r["id"]))
                keep.add(r["id"])
            else:
                keep.add(con().execute("""INSERT INTO article_sources(manufacturer_id, supplier, mfr_art_nr,
                             designation, last_price, preferred, sort, article_id, last_price_at)
                             VALUES (?,?,?,?,?,?,?,?,?)""",
                                       vals + (aid, db.now() if r["last_price"] is not None else None)).lastrowid)
        for (sid,) in q("SELECT id FROM article_sources WHERE article_id = ? AND active = 1", (aid,)):
            if sid not in keep:  # nur ausblenden – Einkaufshistorie bleibt erhalten
                con().execute("UPDATE article_sources SET active = 0, preferred = 0 WHERE id = ?", (sid,))
        has_purchase = q("SELECT 1 FROM movements WHERE article_id = ? AND type = 'IN'"
                         " AND unit_price IS NOT NULL LIMIT 1", (aid,), one=True)
        if not has_purchase:  # noch nichts eingekauft: Preis der Hauptquelle als „letzter EK“
            main = next((r for r in rows if r["preferred"]), None)
            con().execute("UPDATE articles SET last_price = ? WHERE id = ?",
                          (main["last_price"] if main else None, aid))

    def form_lists():
        units = [r[0] for r in q("SELECT DISTINCT unit FROM articles ORDER BY unit")]
        return {"cats": q("SELECT * FROM categories ORDER BY sort, name"),
                "mfrs": q("SELECT * FROM manufacturers ORDER BY name"),
                "units": list(dict.fromkeys(db.DEFAULT_UNITS + units))}

    def validate_article(data, aid=None):
        if not data["art_nr"]:
            return "Bitte eine Artikelnummer angeben (oder eine Kategorie wählen)."
        if not data["name"]:
            return "Bitte eine Bezeichnung angeben."
        if q("SELECT id FROM articles WHERE art_nr = ? COLLATE NOCASE AND id IS NOT ?",
             (data["art_nr"], aid), one=True):
            return f"Artikelnummer {data['art_nr']} ist bereits vergeben."
        return None

    @app.route("/artikel/neu", methods=["GET", "POST"])
    def article_new():
        if request.method == "POST":
            data = article_form_data()
            srcs = form_sources()
            f = request.form
            start = {"stock": max(to_float(f.get("stock"), 0), 0), "lot_nr": f.get("lot_nr", "").strip(),
                     "expires": to_date(f.get("expires"))}
            if not data["art_nr"] and data["category_id"]:
                data["art_nr"] = db.next_art_nr(con(), data["category_id"])
            err = validate_article(data)
            if err:
                flash(err, "error")
                return render_template("article_form.html", art=data, new=True, sources=srcs, start=start,
                                       **form_lists())
            data["photo"] = save_photo(request.files.get("photo"))
            ts = db.now()
            cols = list(data) + ["stock", "created_at", "updated_at"]
            aid = con().execute(f"INSERT INTO articles({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                list(data.values()) + [0, ts, ts]).lastrowid
            save_sources(aid, srcs)
            if start["stock"]:
                db.book(con(), aid, "CORR", start["stock"], user=current_user() or "Lager", note="Anfangsbestand")
                if start["expires"] or start["lot_nr"]:
                    con().execute("UPDATE lots SET expires = ?, lot_nr = ? WHERE article_id = ?",
                                  (start["expires"], start["lot_nr"] or None, aid))
            flash(f"Artikel {data['art_nr']} angelegt.", "ok")
            if f.get("next"):
                return redirect(url_for("article_new", kategorie=data["category_id"]))
            return redirect(url_for("article", aid=aid))
        cat = to_int(request.args.get("kategorie"))
        preset = {"category_id": cat, "unit": "Eimer", "min_stock": 0,
                  "art_nr": db.next_art_nr(con(), cat) if cat else ""}
        return render_template("article_form.html", art=preset, new=True, sources=[], start={}, **form_lists())

    @app.route("/api/naechste-nummer")
    def api_next_nr():
        return {"art_nr": db.next_art_nr(con(), to_int(request.args.get("kategorie"))) or ""}

    @app.route("/artikel/<int:aid>/bearbeiten", methods=["GET", "POST"])
    def article_edit(aid):
        art = q(ARTICLE_SELECT + " WHERE a.id = ?", (aid,), one=True) or abort(404)
        if request.method == "POST":
            data = article_form_data()
            srcs = form_sources()
            err = validate_article(data, aid)
            if err:
                flash(err, "error")
                merged = dict(art)
                merged.update(data)
                return render_template("article_form.html", art=merged, new=False, sources=srcs, **form_lists())
            photo = save_photo(request.files.get("photo"))
            if photo or request.form.get("remove_photo"):
                data["photo"] = photo
            data["updated_at"] = db.now()
            con().execute(f"UPDATE articles SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?",
                          list(data.values()) + [aid])
            save_sources(aid, srcs)
            flash("Artikel gespeichert.", "ok")
            return redirect(url_for("article", aid=aid))
        return render_template("article_form.html", art=art, new=False, sources=sources_of(aid), **form_lists())

    @app.route("/artikel/<int:aid>/aktiv", methods=["POST"])
    def article_toggle(aid):
        art = q("SELECT active FROM articles WHERE id = ?", (aid,), one=True) or abort(404)
        con().execute("UPDATE articles SET active = ?, updated_at = ? WHERE id = ?",
                      (0 if art["active"] else 1, db.now(), aid))
        flash("Artikel archiviert." if art["active"] else "Artikel wieder aktiviert.", "ok")
        return redirect(url_for("article", aid=aid))

    @app.route("/charge/<int:lid>", methods=["POST"])
    def lot_edit(lid):
        lot = q("SELECT * FROM lots WHERE id = ?", (lid,), one=True) or abort(404)
        con().execute("UPDATE lots SET expires = ?, lot_nr = ? WHERE id = ?",
                      (to_date(request.form.get("expires")), request.form.get("lot_nr", "").strip() or None, lid))
        flash("Charge gespeichert.", "ok")
        return redirect(request.form.get("back") or url_for("article", aid=lot["article_id"]))

    @app.route("/foto/<path:name>")
    def photo(name):
        return send_from_directory(PHOTO_DIR, name)

    # ------------------------------------------------------------ Buchen
    def find_articles(key):
        key = key.strip()
        exact = q(ARTICLE_SELECT + " WHERE a.active = 1 AND a.art_nr = ? COLLATE NOCASE", (key,), one=True)
        if exact:
            return [exact], None
        src = q("""SELECT s.id, s.article_id FROM article_sources s JOIN articles a ON a.id = s.article_id
                   WHERE s.active = 1 AND a.active = 1 AND s.mfr_art_nr = ? COLLATE NOCASE""", (key,))
        if len({r["article_id"] for r in src}) == 1:
            return [q(ARTICLE_SELECT + " WHERE a.id = ?", (src[0]["article_id"],), one=True)], src[0]["id"]
        like = f"%{key}%"
        rows = q(ARTICLE_SELECT + " WHERE a.active = 1 AND (a.art_nr LIKE ? OR a.name LIKE ? OR a.color LIKE ?"
                 " OR a.location LIKE ? OR " + SOURCE_SEARCH + ") ORDER BY a.art_nr LIMIT 60", [like] * 8)
        return rows, None

    @app.route("/buchen", methods=["GET", "POST"])
    def booking():
        if request.method == "POST":
            f = request.form
            mtype = f.get("type")
            user = f.get("user", "").strip()
            art = q("SELECT * FROM articles WHERE id = ?", (to_int(f.get("article_id")),), one=True)
            qty = to_float(f.get("qty"))
            back = f.get("back") or url_for("booking", type=mtype)
            if art is None:
                flash("Artikel nicht gefunden.", "error")
                return redirect(back)
            if not user:
                flash("Bitte deinen Namen angeben.", "error")
                return redirect(back)
            if qty is None or qty < 0 or (qty <= db.EPS and mtype != "CORR"):
                flash("Bitte eine gültige Menge angeben (z. B. 2 oder 0,5).", "error")
                return redirect(back)
            ok, msg = db.book(con(), art["id"], mtype, qty, user=user,
                              site=f.get("site", "").strip() or None,
                              note=f.get("note", "").strip() or None,
                              unit_price=to_float(f.get("unit_price")) if mtype == "IN" else None,
                              source_id=to_int(f.get("source_id")) if mtype == "IN" else None,
                              lot_nr=f.get("lot_nr", "").strip() if mtype == "IN" else None,
                              expires=to_date(f.get("expires")) if mtype == "IN" else None,
                              lot_id=to_int(f.get("lot_id")) if mtype in ("OUT", "WASTE") else None)
            flash(f"{TYPE_LABELS.get(mtype, '')} {art['art_nr']} – {msg}", "ok" if ok else "error")
            if ok:
                after = q("SELECT * FROM articles WHERE id = ?", (art["id"],), one=True)
                if after["stock"] <= after["min_stock"] + db.EPS:
                    flash(f"Achtung: {after['art_nr']} {after['name']} ist "
                          f"{'LEER' if after['stock'] <= db.EPS else 'unter Meldebestand'} "
                          f"({db.fmt(after['stock'])} / Min. {db.fmt(after['min_stock'])} {after['unit']}).", "warn")
                if mtype == "OUT":
                    gone = [lt for lt in q("""SELECT l.* FROM lot_moves lm JOIN lots l ON l.id = lm.lot_id
                                              WHERE lm.movement_id = (SELECT MAX(id) FROM movements
                                                                      WHERE article_id = ? AND type = 'OUT')""",
                                           (art["id"],))
                            if db.expiry_state(lt["expires"]) == "abgelaufen"]
                    if gone:
                        flash("Achtung: entnommene Ware ist abgelaufen (" + d(gone[0]["expires"]) + ").", "warn")
            return remember_user(redirect(back), user)

        mtype = request.args.get("type", "OUT")
        if mtype not in TYPE_LABELS:
            mtype = "OUT"
        art, matches, source_id = None, None, to_int(request.args.get("quelle"))
        search = request.args.get("suche", "").strip()
        if request.args.get("artikel"):
            art = q(ARTICLE_SELECT + " WHERE a.id = ?", (to_int(request.args.get("artikel")),), one=True)
        elif search:
            matches, found_src = find_articles(search)
            if len(matches) == 1:
                art, source_id = matches[0], found_src
            elif not matches:
                flash(f"Kein Artikel zu „{search}“ gefunden.", "error")
        sources = sources_of(art["id"]) if art else []
        if art and not source_id and sources:
            source_id = sources[0]["id"]
        lots = q("SELECT * FROM lots WHERE article_id = ? AND qty > 0.000001 ORDER BY expires IS NULL, expires, id",
                 (art["id"],)) if art else []
        all_arts = q("SELECT art_nr, name, color, stock, unit FROM articles WHERE active = 1 ORDER BY art_nr")
        back = url_for("booking", type=mtype, artikel=request.args.get("artikel") or None)
        return render_template("booking.html", mtype=mtype, art=art, all_arts=all_arts, sites=known_sites(),
                               matches=matches if not art else None, search=search, sources=sources,
                               source_id=source_id, back=back, lots=lots,
                               lot_id=to_int(request.args.get("charge")))

    # ------------------------------------------------------------ Bewegungen
    MOVE_SQL = """SELECT mv.*, ar.art_nr, ar.name, ar.unit, ar.color, c.name AS category,
                         sm.name AS src_mfr, s.supplier AS src_supplier FROM movements mv
                  JOIN articles ar ON ar.id = mv.article_id
                  LEFT JOIN categories c ON c.id = ar.category_id
                  LEFT JOIN article_sources s ON s.id = mv.source_id
                  LEFT JOIN manufacturers sm ON sm.id = s.manufacturer_id"""

    def move_rows_csv(rows):
        return (["Datum", "Art", "Artikelnr.", "Bezeichnung", "Farbton", "Kategorie", "Menge", "Einheit",
                 "Bestand danach", "Preis/Einheit", "Wert", "Name", "Baustelle", "Hersteller (Eingang)",
                 "Lieferant (Eingang)", "Notiz"],
                [[dt(r["created_at"]), TYPE_LABELS[r["type"]], r["art_nr"], r["name"], r["color"], r["category"],
                  de_qty(r["qty"]), r["unit"], de_qty(r["stock_after"]), de_num(r["unit_price"]),
                  de_num(abs(r["qty"]) * r["unit_price"] if r["unit_price"] is not None else None),
                  r["user"], r["site"], r["src_mfr"], r["src_supplier"], r["note"]] for r in rows])

    @app.route("/bewegungen")
    def movements():
        a = request.args
        start = a.get("von") or (date.today() - timedelta(days=30)).isoformat()
        end = a.get("bis") or date.today().isoformat()
        where = ["mv.created_at >= ?", "mv.created_at < date(?, '+1 day')"]
        args = [start, end]
        if a.get("type") in TYPE_LABELS:
            where.append("mv.type = ?")
            args.append(a.get("type"))
        if to_int(a.get("kategorie")):
            where.append("ar.category_id = ?")
            args.append(to_int(a.get("kategorie")))
        if a.get("q"):
            like = f"%{a.get('q').strip()}%"
            where.append("(ar.art_nr LIKE ? OR ar.name LIKE ? OR ar.color LIKE ? OR mv.user LIKE ?"
                         " OR mv.site LIKE ? OR mv.note LIKE ?)")
            args += [like] * 6
        sql = MOVE_SQL + " WHERE " + " AND ".join(where) + " ORDER BY mv.created_at DESC, mv.id DESC"
        if a.get("export"):
            return csv_response("bewegungen.csv", *move_rows_csv(q(sql, args)))
        rows = q(sql + " LIMIT 1000", args)
        cats = q("SELECT * FROM categories ORDER BY sort, name")
        return render_template("movements.html", rows=rows, cats=cats, start=start, end=end, a=a)

    # ------------------------------------------------------------ Bestellliste & Haltbarkeit
    @app.route("/bestellliste")
    def order_list():
        rows = low_stock_articles()
        srcs = sources_by_article(r["id"] for r in rows)
        if request.args.get("export"):
            return csv_response("bestellliste.csv",
                                ["Artikelnr.", "Bezeichnung", "Farbton", "Gebinde", "Kategorie", "Bestand",
                                 "Meldebestand", "Einheit", "Hauptquelle", "Hersteller", "Hersteller-Bezeichnung",
                                 "Bestellnr.", "Lieferant", "Preis"],
                                [[r["art_nr"], r["name"], r["color"], r["pack_size"], r["category"],
                                  de_qty(r["stock"]), de_qty(r["min_stock"]), r["unit"],
                                  "ja" if s["preferred"] else "", s["manufacturer"], s["designation"],
                                  s["mfr_art_nr"], s["supplier"], de_num(s["last_price"])]
                                 for r in rows for s in (srcs.get(r["id"]) or [NO_SOURCE])])
        return render_template("order_list.html", rows=rows, srcs=srcs)

    @app.route("/haltbarkeit")
    def expiry():
        show_all = request.args.get("alle") == "1"
        rows = expiring_lots(only_warn=not show_all)
        return render_template("expiry.html", rows=rows, show_all=show_all)

    # ------------------------------------------------------------ Baustellen
    @app.route("/baustellen")
    def sites():
        search = request.args.get("q", "").strip()
        rows = q(f"""SELECT mv.site AS name, COALESCE({COST_SQL},0) AS cost, COUNT(*) AS n,
                            MIN(mv.created_at) AS first, MAX(mv.created_at) AS last
                     FROM movements mv WHERE mv.site IS NOT NULL AND mv.site LIKE ?
                     GROUP BY mv.site ORDER BY MAX(mv.created_at) DESC""", (f"%{search}%",))
        out = {r["site"]: r["n"] for r in q("""SELECT site, COUNT(*) AS n FROM loans
                                               WHERE back_at IS NULL AND site IS NOT NULL GROUP BY site""")}
        return render_template("sites.html", rows=rows, search=search, devices_out=out)

    @app.route("/baustelle")
    def site():
        name = request.args.get("name", "").strip()
        if not name:
            return redirect(url_for("sites"))
        materials = q(f"""SELECT a.id, a.art_nr, a.name, a.unit, a.color, COALESCE({NET_QTY_SQL},0) AS qty,
                                 COALESCE({COST_SQL},0) AS cost
                          FROM movements mv JOIN articles a ON a.id = mv.article_id
                          WHERE mv.site = ? AND mv.type IN ('OUT','RET')
                          GROUP BY a.id ORDER BY cost DESC""", (name,))
        moves = q(MOVE_SQL + " WHERE mv.site = ? ORDER BY mv.created_at DESC, mv.id DESC", (name,))
        loans = q("""SELECT l.*, dv.inv_nr, dv.name FROM loans l JOIN devices dv ON dv.id = l.device_id
                     WHERE l.site = ? ORDER BY l.back_at IS NOT NULL, l.id DESC""", (name,))
        if request.args.get("export"):
            header, rows = move_rows_csv(moves)
            return csv_response("baustelle.csv", header, rows)
        total = sum(m["cost"] for m in materials)
        return render_template("site.html", name=name, materials=materials, moves=moves, loans=loans, total=total)

    # ------------------------------------------------------------ Geräte
    def device_form_data():
        f = request.form
        return {"inv_nr": f.get("inv_nr", "").strip(), "name": f.get("name", "").strip(),
                "category": f.get("category", "").strip() or None,
                "manufacturer": f.get("manufacturer", "").strip() or None,
                "serial_nr": f.get("serial_nr", "").strip() or None,
                "purchase_date": to_date(f.get("purchase_date")), "price": to_float(f.get("price")),
                "location": f.get("location", "").strip() or None,
                "state": f.get("state") if f.get("state") in DEVICE_STATES else "ok",
                "note": f.get("note", "").strip() or None}

    def device_categories():
        used = [r[0] for r in q("SELECT DISTINCT category FROM devices WHERE category IS NOT NULL")]
        return list(dict.fromkeys(DEVICE_CATEGORIES + used))

    @app.route("/geraete")
    def devices():
        search = request.args.get("q", "").strip()
        flt = request.args.get("filter", "")
        where, args = ["dv.active = " + ("0" if flt == "inaktiv" else "1")], []
        if search:
            like = f"%{search}%"
            where.append("(dv.inv_nr LIKE ? OR dv.name LIKE ? OR dv.category LIKE ? OR dv.manufacturer LIKE ?"
                         " OR dv.serial_nr LIKE ? OR ln.person LIKE ? OR ln.site LIKE ?)")
            args += [like] * 7
        if flt == "lager":
            where.append("ln.id IS NULL")
        elif flt == "unterwegs":
            where.append("ln.id IS NOT NULL")
        elif flt == "defekt":
            where.append("dv.state != 'ok'")
        rows = q(DEVICE_SELECT + " WHERE " + " AND ".join(where) + " ORDER BY dv.category, dv.name", args)
        return render_template("devices.html", rows=rows, search=search, flt=flt)

    @app.route("/geraete/<int:did>")
    def device(did):
        dev = q(DEVICE_SELECT + " WHERE dv.id = ?", (did,), one=True) or abort(404)
        loans = q("SELECT * FROM loans WHERE device_id = ? ORDER BY id DESC", (did,))
        return render_template("device.html", dev=dev, loans=loans, sites=known_sites(), people=known_people())

    @app.route("/geraete/neu", methods=["GET", "POST"])
    @app.route("/geraete/<int:did>/bearbeiten", methods=["GET", "POST"])
    def device_form(did=None):
        dev = q("SELECT * FROM devices WHERE id = ?", (did,), one=True) if did else None
        if did and dev is None:
            abort(404)
        if request.method == "POST":
            data = device_form_data()
            if not data["inv_nr"]:
                data["inv_nr"] = db.next_inv_nr(con())
            err = None
            if not data["name"]:
                err = "Bitte eine Bezeichnung angeben."
            elif q("SELECT 1 FROM devices WHERE inv_nr = ? COLLATE NOCASE AND id IS NOT ?",
                   (data["inv_nr"], did), one=True):
                err = f"Inventarnummer {data['inv_nr']} ist bereits vergeben."
            if err:
                flash(err, "error")
                data.update(id=did, photo=dev["photo"] if dev else None)
                return render_template("device_form.html", dev=data, new=not did, cats=device_categories())
            photo = save_photo(request.files.get("photo"))
            if photo or request.form.get("remove_photo"):
                data["photo"] = photo
            data["updated_at"] = db.now()
            if did:
                con().execute(f"UPDATE devices SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?",
                              list(data.values()) + [did])
            else:
                data["created_at"] = data["updated_at"]
                did = con().execute(f"INSERT INTO devices({','.join(data)}) VALUES ({','.join('?' * len(data))})",
                                    list(data.values())).lastrowid
            flash(f"Gerät {data['inv_nr']} gespeichert.", "ok")
            if request.form.get("next"):
                return redirect(url_for("device_form"))
            return redirect(url_for("device", did=did))
        preset = dev or {"inv_nr": db.next_inv_nr(con()), "state": "ok"}
        return render_template("device_form.html", dev=preset, new=not did, cats=device_categories())

    @app.route("/geraete/<int:did>/ausgeben", methods=["POST"])
    def device_out(did):
        dev = q(DEVICE_SELECT + " WHERE dv.id = ?", (did,), one=True) or abort(404)
        f = request.form
        person, user = f.get("person", "").strip(), f.get("user", "").strip()
        if dev["loan_id"]:
            flash(f"{dev['inv_nr']} ist bereits ausgegeben an {dev['person']}.", "error")
        elif not person or not user:
            flash("Bitte angeben, wer das Gerät bekommt, und deinen Namen.", "error")
        else:
            con().execute("""INSERT INTO loans(device_id, person, site, out_at, due_at, out_user, note)
                             VALUES (?,?,?,?,?,?,?)""",
                          (did, person, f.get("site", "").strip() or None, db.now(), to_date(f.get("due_at")),
                           user, f.get("note", "").strip() or None))
            flash(f"{dev['inv_nr']} {dev['name']} ausgegeben an {person}.", "ok")
        return remember_user(redirect(f.get("back") or url_for("device", did=did)), user)

    @app.route("/geraete/<int:did>/zurueck", methods=["POST"])
    def device_back(did):
        dev = q(DEVICE_SELECT + " WHERE dv.id = ?", (did,), one=True) or abort(404)
        f = request.form
        user = f.get("user", "").strip() or current_user() or "Lager"
        if not dev["loan_id"]:
            flash("Gerät ist nicht ausgegeben.", "error")
        else:
            con().execute("UPDATE loans SET back_at = ?, back_user = ?, back_note = ? WHERE id = ?",
                          (db.now(), user, f.get("back_note", "").strip() or None, dev["loan_id"]))
            if f.get("state") in DEVICE_STATES:
                con().execute("UPDATE devices SET state = ?, updated_at = ? WHERE id = ?",
                              (f.get("state"), db.now(), did))
            flash(f"{dev['inv_nr']} {dev['name']} ist zurück im Lager.", "ok")
        return redirect(f.get("back") or url_for("device", did=did))

    @app.route("/geraete/<int:did>/aktiv", methods=["POST"])
    def device_toggle(did):
        dev = q("SELECT active FROM devices WHERE id = ?", (did,), one=True) or abort(404)
        con().execute("UPDATE devices SET active = ?, updated_at = ? WHERE id = ?",
                      (0 if dev["active"] else 1, db.now(), did))
        flash("Gerät ausgemustert." if dev["active"] else "Gerät wieder aktiv.", "ok")
        return redirect(url_for("device", did=did))

    # ------------------------------------------------------------ Analyse
    @app.route("/analyse")
    def analysis():
        period = request.args.get("zeitraum", "monat")
        if period not in ("woche", "monat", "jahr"):
            period = "monat"
        try:
            ref = date.fromisoformat(request.args.get("datum", ""))
        except ValueError:
            ref = date.today()
        start, end, label, prev_ref, next_ref = period_range(period, ref)
        rng = (start.isoformat(), end.isoformat())
        base = ("FROM movements mv JOIN articles a ON a.id = mv.article_id"
                " WHERE mv.created_at >= ? AND mv.created_at < ?")

        totals = q(f"""SELECT COALESCE({COST_SQL},0) AS cost,
            COALESCE(SUM(CASE WHEN mv.type='IN' THEN mv.qty*COALESCE(mv.unit_price,0) END),0) AS in_cost,
            COALESCE(SUM(CASE WHEN mv.type='WASTE' THEN -mv.qty*COALESCE(mv.unit_price,0) END),0) AS waste,
            COUNT(DISTINCT mv.site) AS sites, COUNT(*) AS n {base}""", rng, one=True)
        top = f"""SELECT a.id, a.art_nr, a.name, a.unit, COALESCE({NET_QTY_SQL},0) AS qty,
                         COALESCE({COST_SQL},0) AS cost {base} AND mv.type IN ('OUT','RET') GROUP BY a.id"""
        top_qty = [r for r in q(top + " ORDER BY qty DESC LIMIT 10", rng) if r["qty"] > db.EPS]
        top_cost = [r for r in q(top + " ORDER BY cost DESC LIMIT 10", rng) if r["cost"] > 0.005]
        by_cat = q(f"""SELECT COALESCE(c.name,'(ohne)') AS name, COALESCE({COST_SQL},0) AS cost
                       FROM movements mv JOIN articles a ON a.id = mv.article_id
                       LEFT JOIN categories c ON c.id = a.category_id
                       WHERE mv.created_at >= ? AND mv.created_at < ? AND mv.type IN ('OUT','RET')
                       GROUP BY 1 HAVING cost > 0.005 ORDER BY cost DESC""", rng)
        by_site = q(f"""SELECT COALESCE(mv.site,'(ohne Baustelle)') AS name, COALESCE({COST_SQL},0) AS cost
                        {base} AND mv.type IN ('OUT','RET') GROUP BY 1 HAVING cost > 0.005
                        ORDER BY cost DESC LIMIT 15""", rng)
        by_mfr = q("""SELECT COALESCE(m.name, s.supplier, '(ohne Angabe)') AS name,
                             SUM(mv.qty*COALESCE(mv.unit_price,0)) AS cost
                      FROM movements mv LEFT JOIN article_sources s ON s.id = mv.source_id
                      LEFT JOIN manufacturers m ON m.id = s.manufacturer_id
                      WHERE mv.created_at >= ? AND mv.created_at < ? AND mv.type='IN'
                      GROUP BY 1 ORDER BY cost DESC""", rng)
        waste = q(f"""SELECT a.id, a.art_nr, a.name, a.unit, SUM(-mv.qty) AS qty,
                             SUM(-mv.qty*COALESCE(mv.unit_price,0)) AS cost {base} AND mv.type = 'WASTE'
                      GROUP BY a.id ORDER BY cost DESC LIMIT 10""", rng)

        if period == "jahr":
            fmt, buckets = "%Y-%m", [(f"{start.year}-{m:02d}", MONTHS[m - 1]) for m in range(1, 13)]
        else:
            fmt, buckets = "%Y-%m-%d", []
            for i in range((end - start).days):
                day = start + timedelta(days=i)
                buckets.append((day.isoformat(),
                                f"{WEEKDAYS[day.weekday()]} {day.day}." if period == "woche" else str(day.day)))
        series = {r["k"]: r for r in q(f"""SELECT strftime('{fmt}', mv.created_at) AS k,
                    COALESCE({COST_SQL},0) AS out_cost,
                    COALESCE(SUM(CASE WHEN mv.type='IN' THEN mv.qty*COALESCE(mv.unit_price,0) END),0) AS in_cost
                    {base} GROUP BY k""", rng)}
        timeline = [(lab, max(series[k]["out_cost"], 0) if k in series else 0,
                     series[k]["in_cost"] if k in series else 0) for k, lab in buckets]
        tl_max = max([max(o, i) for _, o, i in timeline] + [0]) or 1

        cutoff = (date.today() - timedelta(days=180)).isoformat()
        slow = q(ARTICLE_SELECT + """ WHERE a.active = 1 AND a.stock > 0 AND NOT EXISTS (
                     SELECT 1 FROM movements mv WHERE mv.article_id = a.id AND mv.type = 'OUT' AND mv.created_at >= ?)
                 ORDER BY a.stock * COALESCE(a.last_price,0) DESC LIMIT 15""", (cutoff,))

        return render_template("analysis.html", period=period, label=label, start=start, end=end - timedelta(days=1),
                               prev_ref=prev_ref, next_ref=next_ref, totals=totals, top_qty=top_qty,
                               top_cost=top_cost, by_cat=by_cat, by_site=by_site, by_mfr=by_mfr, waste=waste,
                               timeline=timeline, tl_max=tl_max, slow=slow)

    # ------------------------------------------------------------ Stammdaten
    @app.route("/stammdaten", methods=["GET", "POST"])
    def master_data():
        if request.method == "POST":
            act = request.form.get("action")
            name = request.form.get("name", "").strip()
            oid = to_int(request.form.get("id"))
            prefix = "".join(ch for ch in request.form.get("prefix", "").upper() if ch.isalnum()) or None
            try:
                if act == "cat_add" and name:
                    nxt = q("SELECT COALESCE(MAX(sort),0)+1 FROM categories", one=True)[0]
                    con().execute("INSERT INTO categories(name, sort, prefix) VALUES (?, ?, ?)", (name, nxt, prefix))
                elif act == "cat_rename" and name and oid:
                    con().execute("UPDATE categories SET name = ?, prefix = ? WHERE id = ?", (name, prefix, oid))
                elif act == "cat_delete" and oid:
                    if q("SELECT 1 FROM articles WHERE category_id = ? LIMIT 1", (oid,), one=True):
                        flash("Kategorie enthält noch Artikel und kann nicht gelöscht werden.", "error")
                    else:
                        con().execute("DELETE FROM categories WHERE id = ?", (oid,))
                elif act in ("cat_up", "cat_down") and oid:
                    cats = [r["id"] for r in q("SELECT id FROM categories ORDER BY sort, name")]
                    i = cats.index(oid)
                    j = i - 1 if act == "cat_up" else i + 1
                    if 0 <= j < len(cats):
                        cats[i], cats[j] = cats[j], cats[i]
                        for pos, cid in enumerate(cats):
                            con().execute("UPDATE categories SET sort = ? WHERE id = ?", (pos, cid))
                elif act == "mfr_add" and name:
                    con().execute("INSERT INTO manufacturers(name) VALUES (?)", (name,))
                elif act == "mfr_rename" and name and oid:
                    con().execute("UPDATE manufacturers SET name = ? WHERE id = ?", (name, oid))
                elif act == "mfr_delete" and oid:
                    if q("SELECT 1 FROM article_sources WHERE manufacturer_id = ? LIMIT 1", (oid,), one=True):
                        flash("Hersteller wird noch von Artikeln verwendet.", "error")
                    else:
                        con().execute("DELETE FROM manufacturers WHERE id = ?", (oid,))
            except db.sqlite3.IntegrityError:
                flash(f"„{name}“ existiert bereits.", "error")
            return redirect(url_for("master_data"))
        cats = q("""SELECT c.*, (SELECT COUNT(*) FROM articles a WHERE a.category_id = c.id) AS n
                    FROM categories c ORDER BY sort, name""")
        mfrs = q("""SELECT m.*, (SELECT COUNT(DISTINCT s.article_id) FROM article_sources s
                    WHERE s.manufacturer_id = m.id AND s.active = 1) AS n FROM manufacturers m ORDER BY name""")
        backups = []
        if os.path.isdir(BACKUP_DIR):
            backups = sorted((f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")), reverse=True)[:10]
        return render_template("master_data.html", cats=cats, mfrs=mfrs, backups=backups,
                               backup_dir=BACKUP_DIR, db_path=DB_PATH)

    # ------------------------------------------------------------ Export / Backup
    @app.route("/export/artikel.csv")
    def export_articles():
        rows = q(ARTICLE_SELECT + " WHERE a.active = 1 ORDER BY c.sort, a.art_nr")
        return csv_response("artikel.csv", *article_rows_csv(rows))

    def article_rows_csv(rows):
        return (["Artikelnr.", "Bezeichnung", "Kategorie", "Farbton", "Gebinde", "Lagerort", "Bestand",
                 "Meldebestand", "Einheit", "Letzter EK", "Lagerwert", "Status", "Nächstes Ablaufdatum",
                 "Hersteller (Haupt)", "Bestellnr. (Haupt)", "Aktiv", "Beschreibung"],
                [[r["art_nr"], r["name"], r["category"], r["color"], r["pack_size"], r["location"],
                  de_qty(r["stock"]), de_qty(r["min_stock"]), r["unit"], de_num(r["last_price"]),
                  de_num(r["stock"] * (r["last_price"] or 0)), status_of(r), d(r["next_expiry"]),
                  r["manufacturer"], r["src_nr"], "ja" if r["active"] else "nein", r["description"]]
                 for r in rows])

    @app.route("/export/alle-daten.zip")
    def export_all():
        tables = {
            "artikel.csv": article_rows_csv(q(ARTICLE_SELECT + " ORDER BY c.sort, a.art_nr")),
            "bezugsquellen.csv": (
                ["Artikelnr.", "Artikel", "Hauptquelle", "Hersteller", "Bezeichnung beim Hersteller", "Bestellnr.",
                 "Lieferant", "Letzter Preis", "Preis vom"],
                [[r["art_nr"], r["name"], "ja" if r["preferred"] else "", r["manufacturer"], r["designation"],
                  r["mfr_art_nr"], r["supplier"], de_num(r["last_price"]), dt(r["last_price_at"])]
                 for r in q("""SELECT s.*, m.name AS manufacturer, a.art_nr, a.name FROM article_sources s
                               JOIN articles a ON a.id = s.article_id
                               LEFT JOIN manufacturers m ON m.id = s.manufacturer_id
                               WHERE s.active = 1 ORDER BY a.art_nr, s.preferred DESC, s.sort""")]),
            "bewegungen.csv": move_rows_csv(q(MOVE_SQL + " ORDER BY mv.id")),
            "chargen_haltbarkeit.csv": (
                ["Artikelnr.", "Bezeichnung", "Chargennr.", "Ablaufdatum", "Menge", "Einheit", "Status"],
                [[r["art_nr"], r["name"], r["lot_nr"], d(r["expires"]), de_qty(r["qty"]), r["unit"],
                  db.expiry_state(r["expires"]) or ""]
                 for r in q("""SELECT l.*, a.art_nr, a.name, a.unit FROM lots l JOIN articles a ON a.id = l.article_id
                               WHERE l.qty > 0.000001 ORDER BY a.art_nr, l.expires""")]),
            "geraete.csv": (
                ["Inventarnr.", "Bezeichnung", "Kategorie", "Hersteller", "Seriennr.", "Kaufdatum", "Preis",
                 "Stammplatz", "Zustand", "Ausgegeben an", "Baustelle", "seit", "geplant zurück", "Aktiv", "Notiz"],
                [[r["inv_nr"], r["name"], r["category"], r["manufacturer"], r["serial_nr"], d(r["purchase_date"]),
                  de_num(r["price"]), r["location"], DEVICE_STATES.get(r["state"]), r["person"], r["loan_site"],
                  dt(r["out_at"]), d(r["due_at"]), "ja" if r["active"] else "nein", r["note"]]
                 for r in q(DEVICE_SELECT + " ORDER BY dv.inv_nr")]),
            "geraete_ausgaben.csv": (
                ["Inventarnr.", "Gerät", "Ausgegeben an", "Baustelle", "Ausgegeben am", "geplant zurück",
                 "Zurück am", "Ausgegeben von", "Zurückgenommen von", "Notiz", "Notiz Rückgabe"],
                [[r["inv_nr"], r["name"], r["person"], r["site"], dt(r["out_at"]), d(r["due_at"]),
                  dt(r["back_at"]), r["out_user"], r["back_user"], r["note"], r["back_note"]]
                 for r in q("""SELECT l.*, dv.inv_nr, dv.name FROM loans l JOIN devices dv ON dv.id = l.device_id
                               ORDER BY l.id""")]),
            "kategorien.csv": (["Kategorie", "Kürzel"],
                               [[r["name"], r["prefix"]] for r in q("SELECT * FROM categories ORDER BY sort")]),
            "hersteller.csv": (["Hersteller"], [[r["name"]] for r in q("SELECT * FROM manufacturers ORDER BY name")]),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, (header, rows) in tables.items():
                zf.writestr(name, csv_bytes(header, rows))
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"lager_alle_daten_{date.today().isoformat()}.zip")

    @app.route("/api/ping")
    def ping():
        return {"app": "dillge-lager"}

    @app.route("/backup", methods=["POST"])
    def make_backup():
        try:
            flash(f"Backup erstellt: {db.backup(DB_PATH, BACKUP_DIR)}", "ok")
        except OSError as exc:
            flash(f"Backup fehlgeschlagen: {exc}", "error")
        return redirect(url_for("master_data"))

    @app.route("/backup/download")
    def download_backup():
        path = db.backup(DB_PATH, BACKUP_DIR)
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))


app = create_app()


# ---------------------------------------------------------------- Start ---

def _port_state(port):
    """'frei', 'lager' (dieses Programm läuft dort schon) oder 'belegt'."""
    import json
    import socket
    import urllib.request
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return "frei"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as resp:
            if json.load(resp).get("app") == "dillge-lager":
                return "lager"
    except Exception:
        pass
    return "belegt"


def main():
    import socket
    import threading
    import webbrowser

    from waitress import serve

    port = int(os.environ.get("LAGER_PORT", "8080"))
    open_browser = os.environ.get("LAGER_NO_BROWSER") != "1"
    print("=" * 64)
    if db.NETWORK_MODE:
        # Jeder Rechner startet sein eigenes Programm; die Daten liegen gemeinsam auf der Netzwerkfestplatte.
        for candidate in range(port, port + 20):
            state = _port_state(candidate)
            if state == "lager":
                print(" Das Lager ist auf diesem Rechner bereits geöffnet – Browser wird geöffnet.")
                if open_browser:
                    webbrowser.open(f"http://localhost:{candidate}")
                return
            if state == "frei":
                port = candidate
                break
        url, host = f"http://localhost:{port}", "127.0.0.1"
        print(" Dillge Lager läuft.")
        print(f"   Adresse:  {url}")
        if open_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    else:
        host = "0.0.0.0"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            host_ip = s.getsockname()[0]
            s.close()
        except OSError:
            host_ip = socket.gethostname()
        print(" Dillge Lager läuft.")
        print(f"   Auf diesem Gerät:  http://localhost:{port}")
        print(f"   Von anderen PCs:   http://{host_ip}:{port}")
    print(f"   Daten:    {DATA_DIR}")
    print(f"   Backups:  {BACKUP_DIR}")
    print(" Fenster offen lassen! Schließen = Programm beendet.")
    print("=" * 64, flush=True)
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
