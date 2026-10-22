# Smart Campus Operations and Facility Booking Platform

A beginner-friendly Flask and SQLite MVP for managing campus rooms, equipment bookings, facility reports, and admin issue triage.

## Implemented so far

- A Flask application factory and a simple responsive starter page.
- A SQLite schema for users, resources, bookings, facility issues, and audit history.
- Automatic, repeatable database initialization and sample data seeding.
- Password hashes for demo accounts (no plain-text passwords are stored in SQLite).
- A `/health` endpoint for a basic application check.
- Login and logout with secure password hash verification.
- Session expiry, signed session cookies, and server-side role checks.
- CSRF checks on state-changing login, logout, booking, issue-reporting, and admin actions.
- An authenticated user dashboard and a role-protected admin dashboard.
- Automated tests for authentication, role permissions, booking behavior, issue reports, and admin workflows.
- Authenticated users can filter active rooms/equipment, check time-slot availability, submit booking requests, view their bookings, and cancel their own pending or approved bookings.
- Overlapping active booking requests are blocked server-side inside a SQLite write transaction; cancelled and rejected bookings do not block a slot.
- Students and staff can submit facility issue reports and view only their own reports. Reports include a title, description, campus location, category, status, and creation time.
- Admins can view all issue reports and change their status to Open, In Progress, or Resolved.
- Admins can approve or reject pending booking requests. Approval rechecks for overlapping active bookings inside a SQLite write transaction.
- The admin dashboard includes database-backed booking and issue totals, status counts, top facilities by approved bookings, and issue counts by category. Admins can optionally filter analytics to a calendar day.
- The admin issue list provides local, explainable category and priority suggestions with matched-term explanations. Admins can explicitly save their final category and priority separately from the reporter's original category and description.

## AI-assisted issue triage

Issue triage runs locally with a deterministic keyword rules engine. It uses report title and description to suggest a category and a priority (Critical, High, Medium, or Low), and shows the matched terms or why the cautious Medium default was used. The existing submitted category remains visible and unchanged. Suggestions are computed for the admin view, not stored as facts; an authenticated admin can explicitly save a final category and priority in separate database fields. The report description is not changed by triage.

This prototype does not train or use a machine-learning model: the project has no suitable labelled issue dataset, and its only application dependency is Flask. The rules are simple heuristics, can miss context or misinterpret wording, and have not been evaluated for accuracy. Treat suggestions as decision support, not emergency assessment or a substitute for administrator review. No issue text is sent to an external service.

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

The database is created automatically at `instance/campus.db`. Existing databases are migrated automatically to add issue title, location, and separate admin triage decision fields. To use another location, set `DATABASE_PATH`.

## Demo accounts

These accounts are seeded on first initialization. Passwords are hashed before insertion.

| Role | Email | Password |
|---|---|---|
| Student | `student@campus.edu` | `Student123!` |
| Staff | `staff@campus.edu` | `Staff123!` |
| Admin | `admin@campus.edu` | `Admin123!` |

The demo accounts are seeded on first initialization and can use the admin issue-management and triage workflows.

## Database overview

- `users`: campus users, role, and password hash.
- `resources`: rooms and equipment.
- `bookings`: requested resource, purpose, date/time, and booking status.
- `facility_issues`: reporter, title, description, campus location, category, priority, status, and timestamps.
- `audit_logs`: actor and details for important actions.

Foreign keys, basic `CHECK` constraints, uniqueness rules, and lookup indexes are defined in `schema.sql`. Initialization is safe to rerun and uses unique keys to avoid duplicate demo records.

## Configuration

Copy `.env.example` as a reference for environment variables. The application reads variables from the process environment; it does not load `.env` files automatically.

- `SECRET_KEY`: Flask signing key. Set a long random value before deployment.
- `DATABASE_PATH`: SQLite database file path (defaults to `instance/campus.db`).
- `SESSION_COOKIE_SECURE`: set to `true` when serving over HTTPS; leave false for local HTTP.
- `HOST`, `PORT`, `FLASK_DEBUG`: local server options.

## Next stages

1. Audit views and notifications.
2. Deployment preparation.

## GitHub and Render

After the remaining application stages are complete:

1. Create a GitHub repository and push this project using `git init`, `git add`, `git commit`, and `git push`.
2. In Render, create a **Web Service** connected to that repository.
3. Set the build command to `pip install -r requirements.txt`.
4. Set the start command to `gunicorn app:app` after adding Gunicorn to `requirements.txt` in the deployment stage.
5. Add a strong `SECRET_KEY` environment variable in Render.

This is a hackathon MVP and should be configured with a strong `SECRET_KEY` before deployment.
