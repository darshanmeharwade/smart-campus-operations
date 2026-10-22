# Smart Campus Operations and Facility Booking Platform

A beginner-friendly Flask and SQLite MVP for managing campus rooms, equipment bookings, and facility reports. The project is being built in stages; this repository currently contains **Stage 1 (application and database foundation), Stage 2 (authentication and role access), and Stage 3 (facility booking)**.

## Implemented so far

- A Flask application factory and a simple responsive starter page.
- A SQLite schema for users, resources, bookings, facility issues, and audit history.
- Automatic, repeatable database initialization and sample data seeding.
- Password hashes for demo accounts (no plain-text passwords are stored in SQLite).
- A `/health` endpoint for a basic application check.
- Login and logout with secure password hash verification.
- Session expiry, signed session cookies, and server-side role checks.
- CSRF checks on login and logout forms.
- An authenticated user dashboard and an admin-only route.
- Automated tests for login, logout, input/CSRF validation, and role permissions.
- Authenticated users can filter active rooms/equipment, check time-slot availability, submit booking requests, view their bookings, and cancel their own pending or approved bookings.
- Overlapping active booking requests are blocked server-side inside a SQLite write transaction; cancelled and rejected bookings do not block a slot.

Facility reporting, admin booking approvals, analytics, and AI assistance are not implemented yet.

## Requirements

- Python 3.10 or newer
- pip

## Run locally

From the project directory, activate your virtual environment and install dependencies:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the application:

```powershell
python app.py
```

Open `http://127.0.0.1:5000/` and sign in with one of the demo accounts. The health check is at `http://127.0.0.1:5000/health`.

To run automated tests:

```powershell
python -m unittest discover -s tests -v
```

The database is created automatically at `instance/campus.db`. To use another location, set `DATABASE_PATH`.

## Demo accounts

These accounts are seeded on first initialization. Passwords are hashed before insertion.

| Role | Email | Password |
|---|---|---|
| Student | `student@campus.edu` | `Student123!` |
| Staff | `staff@campus.edu` | `Staff123!` |
| Admin | `admin@campus.edu` | `Admin123!` |

The demo accounts are seeded on first initialization and are ready for Stage 2 sign-in.

## Database overview

- `users`: campus users, role, and password hash.
- `resources`: rooms and equipment.
- `bookings`: requested resource, purpose, date/time, and booking status.
- `facility_issues`: category, description, priority, and issue status.
- `audit_logs`: actor and details for important actions.

Foreign keys, basic `CHECK` constraints, uniqueness rules, and lookup indexes are defined in `schema.sql`. Initialization is safe to rerun and uses unique keys to avoid duplicate demo records.

## Configuration

Copy `.env.example` as a reference for environment variables. The application reads variables from the process environment; it does not load `.env` files automatically.

- `SECRET_KEY`: Flask signing key. Set a long random value before deployment.
- `DATABASE_PATH`: SQLite database file path (defaults to `instance/campus.db`).
- `SESSION_COOKIE_SECURE`: set to `true` when serving over HTTPS; leave false for local HTTP.
- `HOST`, `PORT`, `FLASK_DEBUG`: local server options.

## Next stages

1. Facility reporting and admin booking approvals.
2. Analytics, audit views, and notifications.
3. Optional AI-assisted report classification and deployment preparation.

## GitHub and Render

After the remaining application stages are complete:

1. Create a GitHub repository and push this project using `git init`, `git add`, `git commit`, and `git push`.
2. In Render, create a **Web Service** connected to that repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `gunicorn app:app` after adding Gunicorn to `requirements.txt` in the deployment stage.
5. Add a strong `SECRET_KEY` environment variable in Render.

The current Stage 1 starter is not yet a complete or deployable booking portal.
