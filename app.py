"""
app.py - the Flask web application (routes, login, permissions).
How to read this file
  1. create_app()          builds the app and registers everything
  2. helpers / decorators  login_required, admin_required
  3. public + auth routes  home, treks, register, login, logout
  4. user routes           dashboard, booking, my bookings, cancel
  5. admin routes          dashboard, trek management, booking management, analytics
"""
import hmac
import os
import re
import secrets
import sqlite3
import time
from datetime import date, timedelta
from functools import wraps
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import database as db
try:                                  # python-dotenv is optional (reads the .env file)
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False
# Easy-to-remember regular user, shown on the login page together with the admin account.
SIMPLE_USER_NAME = "Demo User"
SIMPLE_USER_EMAIL = "user@gmail.com"
SIMPLE_USER_PASSWORD = "user@123"
# (name, location, difficulty, days, price, capacity, days_from_today, description)
TREKS = [
    ("Kodachadri Peak Trek", "Shimoga, Karnataka", "Moderate", 2, 3200, 20, 18,
     "A forest trail to a scenic peak above the Western Ghats with a night camp near the top."),
    ("Meesapulimala Ridge Walk", "Munnar, Kerala", "Difficult", 3, 5400, 12, 30,
     "A demanding ridge walk through tea country and shola forest, ending above the clouds."),
    ("Kudremukh Grassland Trek", "Chikkamagaluru, Karnataka", "Moderate", 2, 2800, 25, 9,
     "Rolling grassland hills with long views of the Western Ghats. Good for first-time trekkers."),
    ("Chembra Peak Day Trek", "Wayanad, Kerala", "Easy", 1, 1200, 30, 6,
     "A one-day climb to the heart-shaped lake. Suitable for beginners and families."),
    ("Tadiandamol Trek", "Coorg, Karnataka", "Moderate", 2, 2600, 18, 45,
     "Climb the highest peak in Coorg through coffee estates and open meadows."),
    ("Agasthyakoodam Expedition", "Thiruvananthapuram, Kerala", "Difficult", 4, 7500, 10, 60,
     "A permit-based multi-day trek into dense forest, home to rare plants and wildlife."),
    ("Nandi Hills Sunrise Walk", "Chikkaballapur, Karnataka", "Easy", 1, 800, 40, 3,
     "An early morning walk to catch the sunrise above the clouds. Light and short."),
    ("Dudhsagar Falls Trail", "Goa", "Easy", 1, 1500, 24, 14,
     "A scenic forest trail to the base of the four-tiered Dudhsagar waterfalls."),
    ("Kolukkumalai Jeep and Trek", "Idukki, Kerala", "Moderate", 2, 3900, 16, 75,
     "Sunrise over one of the highest tea estates, reached by a jeep ride and short trek."),
    ("Harishchandragad Night Trek", "Ahmednagar, Maharashtra", "Difficult", 2, 3600, 15, 22,
     "A steep night climb to a hill fort, with cave camping and the Konkan Kada cliff view."),
    ("Valparai Rainforest Walk", "Coimbatore, Tamil Nadu", "Easy", 1, 1800, 20, -25,
     "A past trek: guided forest walk through the Anamalai rainforest."),
    ("Brahmagiri Ridge Trek", "Coorg, Karnataka", "Moderate", 2, 2900, 14, -12,
     "A past trek: ridge trail on the Kerala-Karnataka border."),
]

# (name, email)  - all fictional
USERS = [
    ("Asha Nair", "asha.nair@example.com"), ("Rohan Mehta", "rohan.mehta@example.com"),
    ("Meera Iyer", "meera.iyer@example.com"), ("Kiran Das", "kiran.das@example.com"),
    ("Sneha Pillai", "sneha.pillai@example.com"), ("Arjun Menon", "arjun.menon@example.com"),
    ("Divya Rao", "divya.rao@example.com"), ("Farhan Ali", "farhan.ali@example.com"),
]

# (user index, trek index, participants, status)
BOOKINGS = [
    (0, 0, 4, "Confirmed"), (1, 0, 3, "Pending"), (2, 0, 2, "Cancelled"),
    (3, 1, 5, "Confirmed"), (4, 1, 4, "Pending"),
    (5, 2, 6, "Confirmed"), (6, 2, 4, "Confirmed"), (0, 2, 3, "Pending"),
    (1, 3, 10, "Confirmed"), (7, 3, 8, "Pending"), (2, 3, 2, "Cancelled"),
    (3, 4, 4, "Confirmed"), (4, 4, 2, "Cancelled"),
    (5, 5, 3, "Pending"), (6, 5, 2, "Confirmed"),
    (7, 6, 12, "Confirmed"), (0, 6, 6, "Confirmed"), (1, 6, 4, "Pending"),
    (2, 7, 5, "Confirmed"), (3, 7, 3, "Cancelled"), (4, 7, 4, "Pending"),
    (5, 8, 2, "Pending"),
    (6, 9, 6, "Confirmed"), (7, 9, 4, "Pending"),
    (0, 10, 5, "Confirmed"), (1, 10, 4, "Confirmed"), (2, 10, 3, "Confirmed"),   # past treks
    (3, 11, 6, "Confirmed"), (4, 11, 2, "Confirmed"),
]

def check_consistency(conn):
    """Return a list of problems (empty list = demo data is consistent)."""
    problems = []
    over = conn.execute(
        """SELECT t.name, t.capacity, SUM(b.participants) AS reserved
           FROM treks t JOIN bookings b ON b.trek_id = t.trek_id
           WHERE b.status IN ('Pending','Confirmed')
           GROUP BY t.trek_id HAVING reserved > t.capacity""").fetchall()
    problems += [f"{r['name']} is overbooked ({r['reserved']}/{r['capacity']})" for r in over]
    dup = conn.execute(
        """SELECT user_id, trek_id FROM bookings WHERE status IN ('Pending','Confirmed')
           GROUP BY user_id, trek_id HAVING COUNT(*) > 1""").fetchall()
    problems += [f"duplicate active booking user {r['user_id']} trek {r['trek_id']}" for r in dup]
    return problems

def seed(conn):
    if conn.execute("SELECT 1 FROM treks").fetchone():
        return False, "Treks already exist, demo data was not added."
    hashed = generate_password_hash(secrets.token_hex(16))   # random: demo accounts are not meant for login
    today = date.today()
    conn.execute("BEGIN IMMEDIATE")
    try:
        trek_ids = []
        for name, loc, diff, days, price, cap, offset, desc in TREKS:
            cur = conn.execute(
                """INSERT INTO treks (name, location, description, difficulty, duration_days,
                                      price, capacity, trek_date, image_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                (name, loc, desc, diff, days, price, cap,
                 (today + timedelta(days=offset)).isoformat(), db.now_str()))
            trek_ids.append(cur.lastrowid)
        user_ids = []
        for name, email in USERS:
            existing = db.get_user_by_email(conn, email)
            if existing:
                user_ids.append(existing["user_id"])
                continue
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'user', ?)",
                (name, email, hashed, db.now_str()))
            user_ids.append(cur.lastrowid)
        for i, (u, t, people, status) in enumerate(BOOKINGS):
            unit_price = TREKS[t][4]
            booked_on = (today - timedelta(days=40 - i)).strftime("%Y-%m-%d 10:00:00")
            conn.execute(
                """INSERT INTO bookings (user_id, trek_id, booking_date, participants, status,
                                         unit_price, total_price) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_ids[u], trek_ids[t], booked_on, people, status, unit_price, unit_price * people))
        problems = check_consistency(conn)
        if problems:
            raise ValueError("; ".join(problems))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return True, (f"Added {len(TREKS)} treks, {len(USERS)} demo users and {len(BOOKINGS)} bookings "
                  f"(all fictional).")

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
def create_app(test_config=None):
    """Application factory: lets the tests create an app with a temporary database."""
    load_dotenv()
    app = Flask(__name__)
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        secret = os.urandom(32).hex()
        if not (test_config and "SECRET_KEY" in test_config):
            print("Note: SECRET_KEY is not set, so a temporary key is used (you will be logged out whenever the server restarts).")

    app.config.update(
        SECRET_KEY=secret,
        DATABASE=db.get_db_path(),
        CHART_DIR=os.path.join(app.root_path, "static", "charts"),
        SESSION_COOKIE_HTTPONLY=True,       # JavaScript cannot read the session cookie
        SESSION_COOKIE_SAMESITE="Lax",      # extra protection against cross-site requests
        # Set SESSION_COOKIE_SECURE=1 in .env when the site runs on HTTPS.
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    db.init_db(app.config["DATABASE"])
    if app.config.get("CREATE_DEFAULT_ADMIN", True):
        setup_conn = db.get_connection(app.config["DATABASE"])
        try:
            db.ensure_default_admin(setup_conn)
        finally:
            setup_conn.close()
    # Skipped in tests, and when DEMO_DATA=0 is set in the environment / .env file.
    if not test_config and os.environ.get("DEMO_DATA", "1") != "0":
        demo_conn = db.get_connection(app.config["DATABASE"])
        try:
            empty = (demo_conn.execute("SELECT COUNT(*) FROM treks").fetchone()[0] == 0
                     and demo_conn.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0] == 0)
            if empty:
                ok, message = seed(demo_conn)
                print(message)
            if demo_conn.execute("SELECT 1 FROM users WHERE email = ?", (SIMPLE_USER_EMAIL,)).fetchone() is None:
                demo_conn.execute(
                    "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'user', ?)",
                    (SIMPLE_USER_NAME, SIMPLE_USER_EMAIL, generate_password_hash(SIMPLE_USER_PASSWORD), db.now_str()))
        finally:
            demo_conn.close()

    # ------------------------------------------------------ CSRF protection
    # Every form contains a secret token that is also kept in the session cookie.
    # A POST whose token is missing or different (e.g. sent from another website)
    # is rejected. This is the same idea as Flask-WTF, written in a few lines.
    def generate_csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(16)
        return session["_csrf_token"]

    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.before_request
    def check_csrf_token():
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and app.config.get("WTF_CSRF_ENABLED", True):
            sent = request.form.get("csrf_token", "") or request.headers.get("X-CSRFToken", "")
            expected = session.get("_csrf_token", "")
            if not expected or not hmac.compare_digest(sent, expected):
                return render_template("error.html", code=400, title="Form expired",
                                       message="The security token for this form is missing or has expired. "
                                               "Go back, reload the page and try again."), 400

    #database per request
    def get_db():
        """One connection per request, closed automatically afterwards."""
        if "db" not in g:
            g.db = db.get_connection(app.config["DATABASE"])
        return g.db

    @app.teardown_appcontext
    def close_db(error=None):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.before_request
    def load_logged_in_user():
        """Look the user up in the database on every request.

        We trust only the user_id in the signed session cookie. The role is
        read fresh from the database, never from the browser.
        """
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = db.get_user_by_id(get_db(), user_id)
            if g.user is None:
                session.clear()

    @app.context_processor
    def inject_globals():
        return {"current_user": g.get("user"), "today": db.today_str()}

    @app.template_filter("money")
    def money(value):
        """Format a number as price, e.g. 2500 -> '₹2,500.00' (change the symbol here)."""
        return f"₹{value:,.2f}"

    #access control
    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if g.user["role"] != "admin":
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def user_required(view):
        """Pages meant for regular users only (admins have their own pages)."""
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if g.user["role"] != "user":
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    #public pages
    @app.route("/")
    def index():
        upcoming = db.list_treks(get_db(), upcoming_only=True, limit=4)
        return render_template("index.html", treks=upcoming)

    @app.route("/treks")
    def treks():
        search = request.args.get("q", "").strip()
        difficulty = request.args.get("difficulty", "")
        sort = request.args.get("sort", "date")
        max_price_text = request.args.get("max_price", "").strip()
        max_price = None
        if max_price_text:
            try:
                max_price = float(max_price_text)
            except ValueError:
                flash("Maximum price must be a number, so that filter was ignored.", "warning")
        rows = db.list_treks(get_db(), search, difficulty, max_price, sort, upcoming_only=True)
        return render_template("treks.html", treks=rows, q=search, difficulty=difficulty,
                               max_price=max_price_text, sort=sort, difficulties=db.DIFFICULTIES)

    def booking_blocked_reason(trek, user):
        """Return a sentence explaining why booking is not possible, or None."""
        if trek["trek_date"] < db.today_str():
            return "This trek date has passed, so booking is closed."
        if trek["available"] <= 0:
            return "This trek is full. Cancelled bookings may free up slots later."
        if user is not None and user["role"] == "user":
            active = get_db().execute(
                "SELECT 1 FROM bookings WHERE user_id = ? AND trek_id = ? AND status IN ('Pending','Confirmed')",
                (user["user_id"], trek["trek_id"])).fetchone()
            if active:
                return "You already have an active booking for this trek (see My Bookings)."
        return None

    @app.route("/treks/<int:trek_id>")
    def trek_details(trek_id):
        trek = db.get_trek(get_db(), trek_id)
        if trek is None:
            abort(404)
        return render_template("trek_details.html", trek=trek,
                               blocked_reason=booking_blocked_reason(trek, g.user))

    #authentication
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("index"))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")
            errors = []
            if not name or len(name) > 100:
                errors.append("Name is required (max 100 characters).")
            if not EMAIL_PATTERN.match(email) or len(email) > 254:
                errors.append("Please enter a valid email address.")
            if len(password) < 8:
                errors.append("Password must be at least 8 characters long.")
            if password != confirm:
                errors.append("Password and confirmation do not match.")
            if not errors and db.get_user_by_email(get_db(), email):
                errors.append("An account with this email already exists.")
            if errors:
                for message in errors:
                    flash(message, "danger")
                return render_template("register.html", name=name, email=email), 400
            try:
                # The role is written here by the server. Any "role" field sent
                # by the browser is simply never read.
                get_db().execute(
                    "INSERT INTO users (name, email, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, 'user', ?)",
                    (name, email, generate_password_hash(password), db.now_str()))
            except sqlite3.IntegrityError:
                flash("An account with this email already exists.", "danger")
                return render_template("register.html", name=name, email=email), 400
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("index"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = db.get_user_by_email(get_db(), email)
            # Same message for "no such email" and "wrong password".
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Invalid email or password.", "danger")
                return render_template("login.html", email=email), 401
            session.clear()                       # drop any old session data
            session["user_id"] = user["user_id"]  # only the ID is stored
            flash(f"Welcome back, {user['name']}!", "success")
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("user_dashboard"))
        if app.config.get("SHOW_LOGIN_HINT", not test_config and os.environ.get("DEMO_DATA", "1") != "0"):
            flash(f"Admin login: {db.DEFAULT_ADMIN_EMAIL} / {db.DEFAULT_ADMIN_PASSWORD}   |   "
                  f"User login: {SIMPLE_USER_EMAIL} / {SIMPLE_USER_PASSWORD}", "info")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out.", "info")
        return redirect(url_for("index"))

    #user pages
    @app.route("/dashboard")
    @user_required
    def user_dashboard():
        conn = get_db()
        uid = g.user["user_id"]
        all_bookings = db.list_user_bookings(conn, uid)
        active = [b for b in all_bookings if b["status"] in db.ACTIVE_STATUSES]
        upcoming = sorted((b for b in active if b["trek_date"] >= db.today_str()),
                          key=lambda b: b["trek_date"])
        return render_template("user_dashboard.html", active_count=len(active),
                               upcoming=upcoming, recent=all_bookings[:5])

    @app.route("/treks/<int:trek_id>/book", methods=["GET", "POST"])
    @user_required
    def book_trek(trek_id):
        conn = get_db()
        trek = db.get_trek(conn, trek_id)
        if trek is None:
            abort(404)
        if request.method == "POST":
            # Only the participant count comes from the form. The user ID is
            # taken from the login session, the price from the database.
            ok, message = db.create_booking(conn, g.user["user_id"], trek_id,
                                            request.form.get("participants", ""))
            flash(message, "success" if ok else "danger")
            return redirect(url_for("my_bookings") if ok else url_for("book_trek", trek_id=trek_id))
        return render_template("booking.html", trek=trek,
                               blocked_reason=booking_blocked_reason(trek, g.user))

    @app.route("/my-bookings")
    @user_required
    def my_bookings():
        status = request.args.get("status", "")
        rows = db.list_user_bookings(get_db(), g.user["user_id"], status)
        return render_template("my_bookings.html", bookings=rows, status=status,
                               statuses=db.ALL_STATUSES)

    @app.route("/bookings/<int:booking_id>")
    @user_required
    def booking_detail(booking_id):
        booking = db.get_booking(get_db(), booking_id)
        # Someone else's booking looks exactly like a missing one (404).
        if booking is None or booking["user_id"] != g.user["user_id"]:
            abort(404)
        return render_template("booking_detail.html", booking=booking)

    @app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
    @login_required
    def cancel_booking(booking_id):
        is_admin = g.user["role"] == "admin"
        ok, message = db.cancel_booking(get_db(), booking_id, g.user["user_id"], is_admin)
        flash(message, "success" if ok else "danger")
        if is_admin:
            return redirect(url_for("manage_bookings"))
        return redirect(url_for("my_bookings"))

    #admin pages
    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        conn = get_db()
        stats = {
            "treks": conn.execute("SELECT COUNT(*) AS c FROM treks").fetchone()["c"],
            "users": conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'user'").fetchone()["c"],
            "active": conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE status IN ('Pending','Confirmed')").fetchone()["c"],
            "pending": conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE status = 'Pending'").fetchone()["c"],
        }
        return render_template("admin_dashboard.html", stats=stats,
                               recent=db.list_all_bookings(conn, limit=5))

    @app.route("/admin/treks")
    @admin_required
    def manage_treks():
        search = request.args.get("q", "").strip()
        rows = db.list_treks(get_db(), search=search, sort="date")
        return render_template("manage_treks.html", treks=rows, q=search)

    @app.route("/admin/treks/new", methods=["GET", "POST"])
    @admin_required
    def add_trek():
        if request.method == "POST":
            clean, errors = db.validate_trek_data(request.form)
            if errors:
                for message in errors:
                    flash(message, "danger")
                return render_template("trek_form.html", trek=request.form, editing=False,
                                       difficulties=db.DIFFICULTIES), 400
            db.create_trek(get_db(), clean)
            flash(f"Trek '{clean['name']}' was added.", "success")
            return redirect(url_for("manage_treks"))
        return render_template("trek_form.html", trek={}, editing=False, difficulties=db.DIFFICULTIES)

    @app.route("/admin/treks/<int:trek_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_trek(trek_id):
        conn = get_db()
        trek = db.get_trek(conn, trek_id)
        if trek is None:
            abort(404)
        if request.method == "POST":
            errors = db.update_trek(conn, trek_id, request.form)
            if errors:
                for message in errors:
                    flash(message, "danger")
                return render_template("trek_form.html", trek=request.form, editing=True,
                                       trek_id=trek_id, reserved=trek["reserved"],
                                       difficulties=db.DIFFICULTIES), 400
            flash("Trek updated. Existing bookings keep the price they were made at.", "success")
            return redirect(url_for("manage_treks"))
        return render_template("trek_form.html", trek=trek, editing=True, trek_id=trek_id,
                               reserved=trek["reserved"], difficulties=db.DIFFICULTIES)

    @app.route("/admin/treks/<int:trek_id>/delete", methods=["POST"])
    @admin_required
    def delete_trek(trek_id):
        ok, message = db.delete_trek(get_db(), trek_id)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("manage_treks"))

    @app.route("/admin/bookings")
    @admin_required
    def manage_bookings():
        search = request.args.get("q", "").strip()
        status = request.args.get("status", "")
        rows = db.list_all_bookings(get_db(), search, status)
        return render_template("manage_bookings.html", bookings=rows, q=search,
                               status=status, statuses=db.ALL_STATUSES)

    @app.route("/admin/bookings/<int:booking_id>/confirm", methods=["POST"])
    @admin_required
    def confirm_booking(booking_id):
        ok, message = db.confirm_booking(get_db(), booking_id)
        flash(message, "success" if ok else "danger")
        return redirect(url_for("manage_bookings"))

    @app.route("/admin/analytics")
    @admin_required
    def analytics_page():
        try:
            import analytics            # needs pandas + matplotlib (loaded only for this page)
        except ImportError:
            flash("The analytics page needs pandas and matplotlib. Run: pip install pandas matplotlib", "warning")
            return redirect(url_for("admin_dashboard"))
        conn = get_db()
        metrics = analytics.get_metrics(conn)
        charts = analytics.generate_charts(conn, app.config["CHART_DIR"])
        return render_template("analytics.html", m=metrics, charts=charts, stamp=int(time.time()))

    # error pages
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, title="Bad request",
                               message="The request could not be understood."), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, title="Access denied",
                               message="You do not have permission to view this page."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, title="Page not found",
                               message="The page you asked for does not exist."), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("error.html", code=500, title="Something went wrong",
                               message="An unexpected error occurred. Please try again."), 500

    return app


if __name__ == "__main__":
    application = create_app()
    # debug=True is for local development only.
    application.run(debug=True)