"""Flask application entry point for the Smart Campus MVP."""

import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Callable

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from db import get_connection, init_db


BASE_DIR = Path(__file__).resolve().parent
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get(
        "SECRET_KEY", secrets.token_hex(32)
    )
    app.config["DATABASE"] = os.environ.get(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "campus.db")
    )
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SECURE"] = (
        os.environ.get("SESSION_COOKIE_SECURE", "").lower() == "true"
    )
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    if test_config is not None:
        app.config.update(test_config)

    init_db(app.config["DATABASE"])

    def destination_for(user) -> str:
        if user["role"] == "admin":
            return url_for("admin_dashboard")
        return url_for("dashboard")

    def dashboard_url() -> str:
        if g.current_user["role"] == "admin":
            return url_for("admin_dashboard")
        return url_for("dashboard")

    def login_required(view: Callable):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if g.current_user is None:
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped_view

    def role_required(required_role: str):
        def decorator(view: Callable):
            @wraps(view)
            @login_required
            def wrapped_view(*args, **kwargs):
                if g.current_user["role"] != required_role:
                    abort(403)
                return view(*args, **kwargs)

            return wrapped_view

        return decorator

    def valid_csrf_token() -> bool:
        submitted_token = request.form.get("csrf_token", "")
        expected_token = session.get("csrf_token", "")
        return (
            isinstance(submitted_token, str)
            and isinstance(expected_token, str)
            and bool(submitted_token)
            and secrets.compare_digest(submitted_token, expected_token)
        )

    def parse_booking_slot(
        booking_date: str, start_time: str, end_time: str
    ) -> tuple[str, str, str] | None:
        try:
            parsed_date = datetime.strptime(booking_date, "%Y-%m-%d").date()
            parsed_start = datetime.strptime(start_time, "%H:%M").time()
            parsed_end = datetime.strptime(end_time, "%H:%M").time()
        except ValueError:
            return None

        now = datetime.now().astimezone()
        requested_start = datetime.combine(
            parsed_date, parsed_start, tzinfo=now.tzinfo
        )
        if (
            parsed_date.isoformat() != booking_date
            or parsed_date < now.date()
            or parsed_start.strftime("%H:%M") != start_time
            or parsed_end.strftime("%H:%M") != end_time
            or parsed_end <= parsed_start
            or requested_start <= now
        ):
            return None
        return booking_date, start_time, end_time

    def dashboard_data(
        availability_date: str = "",
        start_time: str = "",
        end_time: str = "",
        resource_type: str = "",
    ) -> dict:
        slot = None
        search_error = None
        if any((availability_date, start_time, end_time)):
            if not all((availability_date, start_time, end_time)):
                search_error = "Enter a date, start time, and end time to check availability."
            else:
                slot = parse_booking_slot(availability_date, start_time, end_time)
                if slot is None:
                    search_error = "Choose a valid date and time range. The date cannot be in the past."

        if resource_type not in ("", "room", "equipment"):
            resource_type = ""

        resources = []
        bookings = []
        with get_connection(app.config["DATABASE"]) as connection:
            query = "SELECT id, name, resource_type, location, capacity FROM resources WHERE is_active = 1"
            parameters: list[object] = []
            if resource_type:
                query += " AND resource_type = ?"
                parameters.append(resource_type)
            query += " ORDER BY resource_type, name"
            resource_rows = connection.execute(query, parameters).fetchall()

            for resource in resource_rows:
                available = None
                if slot is not None:
                    conflict = connection.execute(
                        """
                        SELECT 1 FROM bookings
                        WHERE resource_id = ? AND booking_date = ?
                          AND status IN ('pending', 'approved')
                          AND start_time < ? AND end_time > ?
                        LIMIT 1
                        """,
                        (resource["id"], slot[0], slot[2], slot[1]),
                    ).fetchone()
                    available = conflict is None
                resources.append({**dict(resource), "available": available})

            bookings = connection.execute(
                """
                SELECT b.id, b.resource_id, r.name AS resource_name, b.purpose,
                       b.booking_date, b.start_time, b.end_time, b.status
                FROM bookings AS b
                JOIN resources AS r ON r.id = b.resource_id
                WHERE b.user_id = ?
                ORDER BY b.booking_date DESC, b.start_time DESC, b.id DESC
                """,
                (g.current_user["id"],),
            ).fetchall()

        return {
            "user": g.current_user,
            "resources": resources,
            "bookings": bookings,
            "availability_date": availability_date,
            "start_time": start_time,
            "end_time": end_time,
            "resource_type": resource_type,
            "search_error": search_error,
            "today": datetime.now().astimezone().date().isoformat(),
        }

    @app.before_request
    def load_current_user() -> None:
        user_id = session.get("user_id")
        g.current_user = None
        if type(user_id) is not int:
            return

        with get_connection(app.config["DATABASE"]) as connection:
            g.current_user = connection.execute(
                "SELECT id, name, email, role FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()

        if g.current_user is None:
            session.clear()

    @app.context_processor
    def inject_csrf_token():
        def csrf_token() -> str:
            token = session.get("csrf_token")
            if not isinstance(token, str):
                token = secrets.token_urlsafe(32)
                session["csrf_token"] = token
            return token

        return {"csrf_token": csrf_token}

    @app.get("/")
    def index():
        if g.current_user is not None:
            return redirect(destination_for(g.current_user))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.current_user is not None:
            return redirect(destination_for(g.current_user))

        email = ""
        error = None
        status_code = 200

        if request.method == "POST":
            email_value = request.form.get("email", "")
            password = request.form.get("password", "")
            email = email_value.strip().lower() if isinstance(email_value, str) else ""

            if not valid_csrf_token():
                error = "Your form session expired. Please try again."
                status_code = 400
            elif (
                not isinstance(password, str)
                or len(email) > 254
                or len(password) > 1024
                or not EMAIL_PATTERN.fullmatch(email)
                or not password
            ):
                error = "Enter a valid email address and password."
                status_code = 400
            else:
                with get_connection(app.config["DATABASE"]) as connection:
                    user = connection.execute(
                        """
                        SELECT id, name, email, role, password_hash
                        FROM users
                        WHERE email = ?
                        """,
                        (email,),
                    ).fetchone()

                if user is None or not check_password_hash(
                    user["password_hash"], password
                ):
                    error = "Email or password is incorrect."
                    status_code = 401
                else:
                    session.clear()
                    session["user_id"] = user["id"]
                    session["csrf_token"] = secrets.token_urlsafe(32)
                    session.permanent = True
                    flash(f"Welcome, {user['name']}!")
                    return redirect(destination_for(user))

        return (
            render_template("login.html", error=error, email=email),
            status_code,
        )

    @app.post("/logout")
    @login_required
    def logout():
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")
        session.clear()
        flash("You have been signed out.")
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        return render_template(
            "dashboard.html",
            **dashboard_data(
                request.args.get("date", "").strip(),
                request.args.get("start_time", "").strip(),
                request.args.get("end_time", "").strip(),
                request.args.get("resource_type", "").strip(),
            ),
        )

    @app.post("/bookings")
    @login_required
    def create_booking():
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        resource_id_value = request.form.get("resource_id", "")
        booking_date = request.form.get("booking_date", "").strip()
        start_time = request.form.get("start_time", "").strip()
        end_time = request.form.get("end_time", "").strip()
        purpose = request.form.get("purpose", "").strip()

        if (
            not resource_id_value.isdecimal()
            or len(booking_date) != 10
            or not all((start_time, end_time))
            or len(purpose) < 1
            or len(purpose) > 500
        ):
            flash("Enter a facility, valid date and time range, and a purpose of 1–500 characters.")
            return redirect(dashboard_url())

        slot = parse_booking_slot(booking_date, start_time, end_time)
        if slot is None:
            flash("Choose a valid date and time range. The date cannot be in the past.")
            return redirect(dashboard_url())

        resource_id = int(resource_id_value)
        try:
            with get_connection(app.config["DATABASE"]) as connection:
                connection.execute("BEGIN IMMEDIATE")
                resource = connection.execute(
                    "SELECT id FROM resources WHERE id = ? AND is_active = 1",
                    (resource_id,),
                ).fetchone()
                if resource is None:
                    flash("That facility is unavailable. Please choose another.")
                    return redirect(dashboard_url())

                conflict = connection.execute(
                    """
                    SELECT 1 FROM bookings
                    WHERE resource_id = ? AND booking_date = ?
                      AND status IN ('pending', 'approved')
                      AND start_time < ? AND end_time > ?
                    LIMIT 1
                    """,
                    (resource_id, slot[0], slot[2], slot[1]),
                ).fetchone()
                if conflict is not None:
                    flash("That facility is already booked during the selected time.")
                    return redirect(dashboard_url())

                now = datetime.now().isoformat(timespec="seconds")
                connection.execute(
                    """
                    INSERT INTO bookings
                        (user_id, resource_id, purpose, booking_date,
                         start_time, end_time, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        g.current_user["id"],
                        resource_id,
                        purpose,
                        slot[0],
                        slot[1],
                        slot[2],
                        now,
                        now,
                    ),
                )
        except sqlite3.Error:
            app.logger.exception("Could not create facility booking")
            flash("We could not save that booking. Please try again.")
            return redirect(dashboard_url())

        flash("Booking request submitted. Status: Pending.")
        return redirect(dashboard_url())

    @app.post("/bookings/<int:booking_id>/cancel")
    @login_required
    def cancel_booking(booking_id: int):
        if not valid_csrf_token():
            abort(400, description="Invalid security token. Refresh and try again.")

        now = datetime.now().isoformat(timespec="seconds")
        with get_connection(app.config["DATABASE"]) as connection:
            cursor = connection.execute(
                """
                UPDATE bookings
                SET status = 'cancelled', updated_at = ?
                WHERE id = ? AND user_id = ?
                  AND status IN ('pending', 'approved')
                """,
                (now, booking_id, g.current_user["id"]),
            )

        if cursor.rowcount == 1:
            flash("Booking cancelled.")
        else:
            flash("That booking cannot be cancelled.")
        return redirect(dashboard_url())

    @app.get("/admin")
    @role_required("admin")
    def admin_dashboard():
        return render_template(
            "dashboard.html",
            **dashboard_data(
                request.args.get("date", "").strip(),
                request.args.get("start_time", "").strip(),
                request.args.get("end_time", "").strip(),
                request.args.get("resource_type", "").strip(),
            ),
            is_admin=True,
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "application": "smart-campus"})

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "").lower() == "true",
    )
