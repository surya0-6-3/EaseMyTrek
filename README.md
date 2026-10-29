# EaseMyTrek

Trekking management and booking analytics system (Flask, SQLite, Bootstrap 5, Pandas + Matplotlib).

## Run (Windows + VS Code)
1. Install Python 3.10+ and open this folder in VS Code.
2. Terminal: `pip install -r requirements.txt`
3. Terminal: `python app.py`
4. Open http://127.0.0.1:5000

The database (`data/easemytrek.db`) is created automatically on first start and filled with
fictional demo treks and bookings. Delete the `data` folder to reset it.

## Logins
| Role  | Email            | Password |
|-------|------------------|----------|
| Admin | admin@trek.com   | admin123 |
| User  | user@gmail.com   | user@123 |

These are demo credentials only - change them before any real deployment.

## Files
- `app.py` - routes, login, permissions, demo data
- `database.py` - SQLite schema and booking logic
- `analytics.py` - metrics and charts for the admin analytics page
- `templates/`, `static/` - Jinja2 pages, Bootstrap, `style.css`
