"""
analytics.py - booking analytics with Pandas and Matplotlib.

Every function reads the real SQLite database (nothing is hard-coded) and
copes with an empty database.

"Active" booking = status Pending or Confirmed (these use capacity).
"""
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # draw to files, no window needed (works on a server)
import matplotlib.pyplot as plt

from database import today_str

ACTIVE = ["Pending", "Confirmed"]
STATUS_ORDER = ["Pending", "Confirmed", "Cancelled"]
FOREST_GREEN = "#2f6b4f"


# ------------------------------------------------------------ load the data
def load_treks(conn):
    """All treks as a DataFrame."""
    return pd.read_sql_query(
        "SELECT trek_id, name, location, difficulty, duration_days, price, capacity, trek_date FROM treks",
        conn)


def load_bookings(conn):
    """All bookings as a DataFrame (password hashes are never selected)."""
    return pd.read_sql_query(
        """SELECT b.booking_id, b.user_id, b.trek_id, t.name AS trek_name, t.difficulty,
                  b.booking_date, b.participants, b.status, b.unit_price, b.total_price
           FROM bookings b JOIN treks t ON t.trek_id = b.trek_id""",
        conn)


# ------------------------------------------------------------- the metrics
def popular_treks(bookings):
    """Participants in ACTIVE bookings per trek, highest first."""
    active = bookings[bookings["status"].isin(ACTIVE)]
    if active.empty:
        return pd.DataFrame(columns=["trek_name", "participants"])
    result = active.groupby("trek_name", as_index=False)["participants"].sum()
    return result.sort_values(["participants", "trek_name"], ascending=[False, True]).reset_index(drop=True)


def occupancy_table(treks, bookings):
    """Occupancy % per trek = active reserved participants / capacity * 100."""
    if treks.empty:
        return pd.DataFrame(columns=["trek_name", "capacity", "reserved", "occupancy_pct", "inconsistent"])
    active = bookings[bookings["status"].isin(ACTIVE)]
    reserved = active.groupby("trek_id")["participants"].sum()
    table = treks[["trek_id", "name", "capacity"]].rename(columns={"name": "trek_name"}).copy()
    table["reserved"] = table["trek_id"].map(reserved).fillna(0).astype(int)
    table["occupancy_pct"] = (table["reserved"] / table["capacity"] * 100).round(1)
    # Reserved above capacity should never happen. Report it, do not hide it.
    table["inconsistent"] = table["reserved"] > table["capacity"]
    return table.drop(columns="trek_id")


def status_counts(bookings):
    counts = bookings["status"].value_counts() if not bookings.empty else pd.Series(dtype=int)
    return counts.reindex(STATUS_ORDER, fill_value=0)


def bookings_by_difficulty(bookings):
    """Number of ACTIVE bookings per difficulty level."""
    levels = ["Easy", "Moderate", "Difficult"]
    active = bookings[bookings["status"].isin(ACTIVE)]
    counts = active["difficulty"].value_counts() if not active.empty else pd.Series(dtype=int)
    return counts.reindex(levels, fill_value=0)


def get_metrics(conn):
    """All headline numbers in one dictionary (used by the admin analytics page)."""
    treks = load_treks(conn)
    bookings = load_bookings(conn)
    # fetchone()[0] works with a plain connection and with sqlite3.Row connections
    users = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'user'").fetchone()[0]

    total_bookings = len(bookings)
    active = bookings[bookings["status"].isin(ACTIVE)]
    cancelled = int((bookings["status"] == "Cancelled").sum()) if total_bookings else 0
    occupancy = occupancy_table(treks, bookings)

    capacity_total = int(treks["capacity"].sum()) if not treks.empty else 0
    reserved_total = int(occupancy["reserved"].sum()) if not occupancy.empty else 0

    return {
        "total_treks": len(treks),
        "upcoming_treks": int((treks["trek_date"] >= today_str()).sum()) if not treks.empty else 0,
        "total_users": users,
        "total_bookings": total_bookings,
        "active_bookings": len(active),
        "cancelled_bookings": cancelled,
        "estimated_value": round(float(active["total_price"].sum()), 2) if not active.empty else 0.0,
        # Cancellation rate counts booking RECORDS, not participants.
        "cancellation_rate": round(cancelled / total_bookings * 100, 1) if total_bookings else 0.0,
        "overall_occupancy": round(reserved_total / capacity_total * 100, 1) if capacity_total else 0.0,
        "inconsistent_treks": occupancy.loc[occupancy["inconsistent"], "trek_name"].tolist() if not occupancy.empty else [],
        "most_popular": popular_treks(bookings).head(5),
    }


# ----------------------------------------------------------------- charts
def _empty_chart(path, title):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.text(0.5, 0.5, "No data available yet", ha="center", va="center", fontsize=13, color="grey")
    ax.set_title(title)
    ax.axis("off")
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def _bar_chart(path, labels, values, title, xlabel, ylabel, ymax=None):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, values, color=FOREST_GREEN)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if ymax:
        ax.set_ylim(0, ymax)
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    for i, v in enumerate(values):                 # print the value above each bar
        ax.text(i, v, f"{v:g}", ha="center", va="bottom", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def generate_charts(conn, out_dir):
    """Create the four PNG charts in out_dir and return their file names."""
    os.makedirs(out_dir, exist_ok=True)
    treks, bookings = load_treks(conn), load_bookings(conn)
    files = {}

    # 1. Most popular treks
    path = os.path.join(out_dir, "popular_treks.png")
    popular = popular_treks(bookings).head(8)
    title = "Most Popular Treks (active participants)"
    if popular.empty:
        _empty_chart(path, title)
    else:
        _bar_chart(path, popular["trek_name"], popular["participants"], title, "Trek", "Participants")
    files["popular"] = "popular_treks.png"

    # 2. Occupancy rates
    path = os.path.join(out_dir, "occupancy.png")
    occ = occupancy_table(treks, bookings)
    title = "Occupancy Rate by Trek (%)"
    if occ.empty:
        _empty_chart(path, title)
    else:
        _bar_chart(path, occ["trek_name"], occ["occupancy_pct"], title, "Trek", "Occupancy (%)",
                   ymax=max(100, occ["occupancy_pct"].max() * 1.1))
    files["occupancy"] = "occupancy.png"

    # 3. Booking status pie chart
    path = os.path.join(out_dir, "status.png")
    counts = status_counts(bookings)
    title = "Booking Status Distribution"
    if counts.sum() == 0:
        _empty_chart(path, title)
    else:
        nonzero = counts[counts > 0]
        colors = {"Pending": "#e0b536", "Confirmed": "#2f6b4f", "Cancelled": "#9aa0a6"}
        fig, ax = plt.subplots(figsize=(5.5, 4.5))
        ax.pie(nonzero, labels=[f"{k} ({v})" for k, v in nonzero.items()], autopct="%1.0f%%",
               colors=[colors[k] for k in nonzero.index], startangle=90)
        ax.set_title(title)
        fig.tight_layout()
        fig.savefig(path, dpi=100)
        plt.close(fig)
    files["status"] = "status.png"

    # 4. Bookings by difficulty
    path = os.path.join(out_dir, "difficulty.png")
    by_diff = bookings_by_difficulty(bookings)
    title = "Active Bookings by Difficulty"
    if by_diff.sum() == 0:
        _empty_chart(path, title)
    else:
        _bar_chart(path, by_diff.index, by_diff.values, title, "Difficulty", "Active bookings")
    files["difficulty"] = "difficulty.png"
    return files
