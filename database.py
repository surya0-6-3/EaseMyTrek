"""
database.py - everything that talks to SQLite.

Contents
  1. Connection + schema creation
  2. Small helper functions (dates, availability)
  3. Trek functions (validate, create, update, delete, list)
  4. Booking functions (create, confirm, cancel)  <- the "business logic"

Only the standard-library sqlite3 module is used (no ORM).
Every SQL statement uses ? placeholders (parameterized queries),
which is what protects us from SQL injection.
"""
import os
import sqlite3
from datetime import date, datetime

from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "easemytrek.db")

# Built-in administrator, created automatically the first time the app starts.
# The password is stored hashed in the database. CHANGE THESE before any real deployment.
DEFAULT_ADMIN_NAME = "Administrator"
DEFAULT_ADMIN_EMAIL = "admin@trek.com"
DEFAULT_ADMIN_PASSWORD = "admin123"

DIFFICULTIES = ("Easy", "Moderate", "Difficult")
ACTIVE_STATUSES = ("Pending", "Confirmed")     # these use up capacity
ALL_STATUSES = ("Pending", "Confirmed", "Cancelled")


def get_db_path():
    """Database location: EASEMYTREK_DB environment variable, else data/easemytrek.db."""
    path = os.environ.get("EASEMYTREK_DB", DEFAULT_DB_PATH)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return path


# ---------------------------------------------------------------- 1. SCHEMA
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS treks (
    trek_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    location      TEXT NOT NULL,
    description   TEXT NOT NULL,
    difficulty    TEXT NOT NULL CHECK (difficulty IN ('Easy', 'Moderate', 'Difficult')),
    duration_days INTEGER NOT NULL CHECK (duration_days > 0),
    price         REAL NOT NULL CHECK (price >= 0),
    capacity      INTEGER NOT NULL CHECK (capacity > 0),
    trek_date     TEXT NOT NULL,
    image_url     TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    trek_id      INTEGER NOT NULL,
    booking_date TEXT NOT NULL,
    participants INTEGER NOT NULL CHECK (participants > 0),
    status       TEXT NOT NULL DEFAULT 'Pending'
                 CHECK (status IN ('Pending', 'Confirmed', 'Cancelled')),
    unit_price   REAL NOT NULL CHECK (unit_price >= 0),
    total_price  REAL NOT NULL CHECK (total_price >= 0),
    CHECK (ABS(total_price - participants * unit_price) < 0.005),
    FOREIGN KEY (user_id) REFERENCES users (user_id),
    FOREIGN KEY (trek_id) REFERENCES treks (trek_id)
);

-- Indexes for columns we search / join on often
CREATE INDEX IF NOT EXISTS idx_bookings_user   ON bookings (user_id);
CREATE INDEX IF NOT EXISTS idx_bookings_trek   ON bookings (trek_id, status);
CREATE INDEX IF NOT EXISTS idx_treks_date      ON treks (trek_date);

-- One ACTIVE (Pending/Confirmed) booking per user per trek.
-- Cancelled rows are ignored by this index, so re-booking after a cancel works.
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_booking
    ON bookings (user_id, trek_id)
    WHERE status IN ('Pending', 'Confirmed');
"""


def get_connection(db_path=None):
    """Open a connection with foreign keys ON (SQLite has them OFF by default).

    isolation_level=None means Python will not start transactions silently;
    we write BEGIN / COMMIT / ROLLBACK ourselves so it is obvious what happens.
    """
    conn = sqlite3.connect(db_path or get_db_path(), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row          # access columns by name: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    """Create the data folder and all tables (safe to run many times)."""
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = get_connection(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def ensure_default_admin(conn):
    """Create the built-in admin account if it does not exist yet (never overwrites it)."""
    if get_user_by_email(conn, DEFAULT_ADMIN_EMAIL) is None:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
            (DEFAULT_ADMIN_NAME, DEFAULT_ADMIN_EMAIL, generate_password_hash(DEFAULT_ADMIN_PASSWORD), now_str()))


# ---------------------------------------------------------------- 2. HELPERS
def today_str():
    return date.today().isoformat()


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_date(text):
    """Return a date object for 'YYYY-MM-DD', or None if invalid."""
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def reserved_participants(conn, trek_id):
    """Participants in Pending + Confirmed bookings (Cancelled do NOT count)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(participants), 0) AS reserved
           FROM bookings
           WHERE trek_id = ? AND status IN ('Pending', 'Confirmed')""",
        (trek_id,),
    ).fetchone()
    return row["reserved"]


def available_slots(conn, trek_id):
    """Capacity minus reserved. Always calculated, never stored."""
    trek = conn.execute("SELECT capacity FROM treks WHERE trek_id = ?", (trek_id,)).fetchone()
    if trek is None:
        return None
    return trek["capacity"] - reserved_participants(conn, trek_id)


# ---------------------------------------------------------------- 3. TREKS
# The SELECT below computes "reserved" and "available" with a sub-query so the
# list pages never need a stored availability column.
TREK_SELECT = """
SELECT t.*,
       COALESCE((SELECT SUM(b.participants) FROM bookings b
                 WHERE b.trek_id = t.trek_id
                   AND b.status IN ('Pending', 'Confirmed')), 0) AS reserved,
       t.capacity - COALESCE((SELECT SUM(b.participants) FROM bookings b
                 WHERE b.trek_id = t.trek_id
                   AND b.status IN ('Pending', 'Confirmed')), 0) AS available
FROM treks t
"""


def get_trek(conn, trek_id):
    return conn.execute(TREK_SELECT + " WHERE t.trek_id = ?", (trek_id,)).fetchone()


def list_treks(conn, search="", difficulty="", max_price=None, sort="date", upcoming_only=False, limit=None):
    """Search / filter / sort treks. All user input goes in as ? parameters."""
    sql = TREK_SELECT + " WHERE 1 = 1"
    params = []
    if search:
        sql += " AND (t.name LIKE ? OR t.location LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if difficulty in DIFFICULTIES:
        sql += " AND t.difficulty = ?"
        params.append(difficulty)
    if max_price is not None:
        sql += " AND t.price <= ?"
        params.append(max_price)
    if upcoming_only:
        sql += " AND t.trek_date >= ?"
        params.append(today_str())
    # ORDER BY text cannot be a ? parameter, so we pick from a fixed dictionary.
    order = {"date": "t.trek_date ASC", "price_low": "t.price ASC", "price_high": "t.price DESC"}
    sql += " ORDER BY " + order.get(sort, order["date"])
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def validate_trek_data(form, existing=None, reserved=0):
    """Check trek form data. Returns (clean_dict, list_of_error_messages).

    existing = the current DB row when editing (None when adding).
    reserved = participants already reserved (capacity can't go below this).
    """
    errors = []
    name = (form.get("name") or "").strip()
    location = (form.get("location") or "").strip()
    description = (form.get("description") or "").strip()
    difficulty = (form.get("difficulty") or "").strip()
    image_url = (form.get("image_url") or "").strip()
    trek_date_text = (form.get("trek_date") or "").strip()

    if not name or len(name) > 100:
        errors.append("Trek name is required (max 100 characters).")
    if not location or len(location) > 100:
        errors.append("Location is required (max 100 characters).")
    if not description:
        errors.append("Description is required.")
    if difficulty not in DIFFICULTIES:
        errors.append("Difficulty must be Easy, Moderate or Difficult.")

    try:
        duration = int(form.get("duration_days", ""))
        if duration <= 0:
            raise ValueError
    except ValueError:
        duration = None
        errors.append("Duration must be a whole number of days greater than 0.")

    try:
        price = float(form.get("price", ""))
        if price < 0 or price != price or price == float("inf"):   # also rejects NaN / inf
            raise ValueError
    except ValueError:
        price = None
        errors.append("Price must be a number that is 0 or more.")

    try:
        capacity = int(form.get("capacity", ""))
        if capacity <= 0:
            raise ValueError
    except ValueError:
        capacity = None
        errors.append("Capacity must be a whole number greater than 0.")
    if capacity is not None and capacity < reserved:
        errors.append(f"Capacity cannot be lower than the {reserved} participants already reserved.")

    trek_date = parse_date(trek_date_text)
    if trek_date is None:
        errors.append("Trek date must be a valid date (YYYY-MM-DD).")
    else:
        unchanged = existing is not None and existing["trek_date"] == trek_date_text
        if trek_date < date.today() and not unchanged:
            errors.append("Trek date cannot be in the past.")

    if image_url and not image_url.lower().startswith(("http://", "https://")):
        errors.append("Image URL must start with http:// or https://")

    clean = {
        "name": name, "location": location, "description": description,
        "difficulty": difficulty, "duration_days": duration, "price": price,
        "capacity": capacity, "trek_date": trek_date_text,
        "image_url": image_url or None,
    }
    return clean, errors


def create_trek(conn, data):
    cur = conn.execute(
        """INSERT INTO treks (name, location, description, difficulty, duration_days,
                              price, capacity, trek_date, image_url, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["name"], data["location"], data["description"], data["difficulty"],
         data["duration_days"], data["price"], data["capacity"], data["trek_date"],
         data["image_url"], now_str()),
    )
    return cur.lastrowid


def update_trek(conn, trek_id, form):
    """Edit a trek. Returns a list of errors (empty list = success).

    The capacity check and the UPDATE happen in one transaction so a booking
    cannot slip in between them. Existing bookings keep their stored
    unit_price, so changing the price here never changes booking history.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT * FROM treks WHERE trek_id = ?", (trek_id,)).fetchone()
        if existing is None:
            conn.execute("ROLLBACK")
            return ["Trek not found."]
        reserved = reserved_participants(conn, trek_id)
        clean, errors = validate_trek_data(form, existing, reserved)
        if errors:
            conn.execute("ROLLBACK")
            return errors
        conn.execute(
            """UPDATE treks SET name=?, location=?, description=?, difficulty=?,
                      duration_days=?, price=?, capacity=?, trek_date=?, image_url=?
               WHERE trek_id=?""",
            (clean["name"], clean["location"], clean["description"], clean["difficulty"],
             clean["duration_days"], clean["price"], clean["capacity"], clean["trek_date"],
             clean["image_url"], trek_id),
        )
        conn.execute("COMMIT")
        return []
    except Exception:
        conn.execute("ROLLBACK")
        raise


def delete_trek(conn, trek_id):
    """Delete a trek only if it has NO bookings at all. Returns (ok, message)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        trek = conn.execute("SELECT name FROM treks WHERE trek_id = ?", (trek_id,)).fetchone()
        if trek is None:
            conn.execute("ROLLBACK")
            return False, "Trek not found."
        count = conn.execute("SELECT COUNT(*) AS c FROM bookings WHERE trek_id = ?", (trek_id,)).fetchone()["c"]
        if count > 0:
            conn.execute("ROLLBACK")
            return False, (f"'{trek['name']}' has {count} booking record(s) and cannot be deleted "
                           "because booking history must be kept.")
        conn.execute("DELETE FROM treks WHERE trek_id = ?", (trek_id,))
        conn.execute("COMMIT")
        return True, f"Trek '{trek['name']}' was deleted."
    except Exception:
        conn.execute("ROLLBACK")
        raise


# -------------------------------------------------------------- 4. BOOKINGS
def create_booking(conn, user_id, trek_id, participants):
    """Create a Pending booking. Returns (ok, message).

    BEGIN IMMEDIATE takes SQLite's write lock straight away. Two people
    booking at the same moment are therefore handled one after the other:
    the second one re-reads the capacity AFTER the first has committed,
    so the trek can never be overbooked.
    """
    try:
        # int(str(...)) rejects "2.5" instead of silently turning it into 2
        participants = int(str(participants).strip())
    except (TypeError, ValueError):
        return False, "Number of participants must be a whole number."
    if participants < 1 or participants > 20:
        return False, "Number of participants must be between 1 and 20."

    conn.execute("BEGIN IMMEDIATE")
    try:
        trek = conn.execute("SELECT * FROM treks WHERE trek_id = ?", (trek_id,)).fetchone()
        if trek is None:
            conn.execute("ROLLBACK")
            return False, "Trek not found."
        if trek["trek_date"] < today_str():
            conn.execute("ROLLBACK")
            return False, "This trek has already taken place and cannot be booked."

        duplicate = conn.execute(
            """SELECT 1 FROM bookings
               WHERE user_id = ? AND trek_id = ? AND status IN ('Pending', 'Confirmed')""",
            (user_id, trek_id),
        ).fetchone()
        if duplicate:
            conn.execute("ROLLBACK")
            return False, "You already have an active booking for this trek."

        available = trek["capacity"] - reserved_participants(conn, trek_id)
        if participants > available:
            conn.execute("ROLLBACK")
            if available <= 0:
                return False, "Sorry, this trek is full."
            return False, f"Only {available} slot(s) are left for this trek."

        unit_price = trek["price"]
        total_price = round(unit_price * participants, 2)
        conn.execute(
            """INSERT INTO bookings (user_id, trek_id, booking_date, participants,
                                     status, unit_price, total_price)
               VALUES (?, ?, ?, ?, 'Pending', ?, ?)""",
            (user_id, trek_id, now_str(), participants, unit_price, total_price),
        )
        conn.execute("COMMIT")
        return True, f"Booking created for {participants} participant(s). Status: Pending."
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        return False, "You already have an active booking for this trek."
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_booking(conn, booking_id):
    return conn.execute(
        """SELECT b.*, t.name AS trek_name, t.location, t.trek_date,
                  u.name AS user_name, u.email AS user_email
           FROM bookings b
           JOIN treks t ON t.trek_id = b.trek_id
           JOIN users u ON u.user_id = b.user_id
           WHERE b.booking_id = ?""",
        (booking_id,),
    ).fetchone()


def confirm_booking(conn, booking_id):
    """Admin action: Pending -> Confirmed. Returns (ok, message)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        booking = get_booking(conn, booking_id)
        if booking is None:
            conn.execute("ROLLBACK")
            return False, "Booking not found."
        if booking["status"] != "Pending":
            conn.execute("ROLLBACK")
            return False, f"Only Pending bookings can be confirmed (this one is {booking['status']})."
        if booking["trek_date"] < today_str():
            conn.execute("ROLLBACK")
            return False, "This trek date has passed, so the booking cannot be confirmed."
        conn.execute("UPDATE bookings SET status = 'Confirmed' WHERE booking_id = ?", (booking_id,))
        conn.execute("COMMIT")
        return True, f"Booking #{booking_id} confirmed."
    except Exception:
        conn.execute("ROLLBACK")
        raise


def cancel_booking(conn, booking_id, acting_user_id, acting_is_admin):
    """Cancel a booking (owner or admin). The row is kept, only the status changes.

    Cancelled bookings no longer count in reserved_participants(), so their
    slots become available again automatically.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        booking = get_booking(conn, booking_id)
        # A missing booking and somebody else's booking give the same answer,
        # so a user cannot discover which booking IDs exist.
        if booking is None or (not acting_is_admin and booking["user_id"] != acting_user_id):
            conn.execute("ROLLBACK")
            return False, "Booking not found."
        if booking["status"] == "Cancelled":
            conn.execute("ROLLBACK")
            return False, "This booking is already cancelled."
        if booking["trek_date"] <= today_str():
            conn.execute("ROLLBACK")
            return False, "Bookings can only be cancelled before the trek date."
        conn.execute("UPDATE bookings SET status = 'Cancelled' WHERE booking_id = ?", (booking_id,))
        conn.execute("COMMIT")
        return True, f"Booking #{booking_id} cancelled."
    except Exception:
        conn.execute("ROLLBACK")
        raise


BOOKING_SELECT = """
SELECT b.*, t.name AS trek_name, t.location, t.trek_date,
       u.name AS user_name, u.email AS user_email
FROM bookings b
JOIN treks t ON t.trek_id = b.trek_id
JOIN users u ON u.user_id = b.user_id
"""


def list_user_bookings(conn, user_id, status=""):
    sql = BOOKING_SELECT + " WHERE b.user_id = ?"
    params = [user_id]
    if status in ALL_STATUSES:
        sql += " AND b.status = ?"
        params.append(status)
    sql += " ORDER BY b.booking_id DESC"
    return conn.execute(sql, params).fetchall()


def list_all_bookings(conn, search="", status="", limit=None):
    sql = BOOKING_SELECT + " WHERE 1 = 1"
    params = []
    if search:
        sql += " AND (u.name LIKE ? OR u.email LIKE ? OR t.name LIKE ?)"
        params += [f"%{search}%"] * 3
    if status in ALL_STATUSES:
        sql += " AND b.status = ?"
        params.append(status)
    sql += " ORDER BY b.booking_id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


# ------------------------------------------------------------------- USERS
def get_user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
