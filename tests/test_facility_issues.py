"""Tests for facility issue reporting and Stage 4 admin actions."""

import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-stage4-import-")
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
try:
    from app import create_app
    from db import get_connection, init_db
finally:
    if _PREVIOUS_DATABASE_PATH is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = _PREVIOUS_DATABASE_PATH


class FacilityIssueAndAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="smart-campus-stage4-test-")
        self.database_path = str(Path(self.temp_dir.name) / "stage4.db")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "SECRET_KEY": "test-only-stage4-session-key",
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()
        self.login_as("student@campus.edu", "Student123!")
        self.booking_date = "2099-01-15"

    def tearDown(self):
        self.temp_dir.cleanup()

    def login_as(self, email, password):
        response = self.client.get("/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            self.fail("Login form did not include its CSRF token.")
        response = self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": match.group(1).decode(),
            },
        )
        self.assertEqual(response.status_code, 302)

    def csrf_token(self, path="/dashboard"):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            self.fail("Page did not include its CSRF token.")
        return match.group(1).decode()

    def submit_issue(self, **values):
        data = {
            "csrf_token": self.csrf_token(),
            "title": "Broken projector",
            "description": "The projector does not power on.",
            "location": "Technology Block, Lab 3",
            "category": "electrical",
        }
        data.update(values)
        return self.client.post("/issues", data=data, follow_redirects=True)

    def issue_rows(self):
        with get_connection(self.database_path) as connection:
            return connection.execute(
                """
                SELECT id, user_id, title, description, location, category,
                       priority, status
                FROM facility_issues
                ORDER BY id
                """
            ).fetchall()

    def submit_booking(self, purpose="Project meeting"):
        return self.client.post(
            "/bookings",
            data={
                "csrf_token": self.csrf_token(),
                "resource_id": "1",
                "booking_date": self.booking_date,
                "start_time": "09:00",
                "end_time": "10:00",
                "purpose": purpose,
            },
            follow_redirects=True,
        )

    def booking_status(self, booking_id):
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT status FROM bookings WHERE id = ?",
                (booking_id,),
            ).fetchone()
        return row["status"] if row else None

    def test_user_can_submit_and_view_own_issue_report(self):
        response = self.submit_issue()
        self.assertIn(b"Facility issue report submitted.", response.data)
        self.assertIn(b"Broken projector", response.data)
        self.assertIn(b"Technology Block, Lab 3", response.data)
        rows = self.issue_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "electrical")
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["priority"], "medium")

    def test_user_cannot_view_another_users_issue_report(self):
        self.submit_issue(title="Private student report")
        self.client = self.app.test_client()
        self.login_as("staff@campus.edu", "Staff123!")

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Private student report", response.data)

    def test_admin_can_view_all_reports_and_update_status(self):
        self.submit_issue(title="Campus plumbing issue", category="plumbing")
        issue_id = self.issue_rows()[0]["id"]
        self.client = self.app.test_client()
        self.login_as("staff@campus.edu", "Staff123!")
        self.submit_issue(title="Staff network issue", category="network")

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")

        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Campus plumbing issue", response.data)
        self.assertIn(b"Staff network issue", response.data)
        self.assertIn(b"Asha Student", response.data)
        self.assertIn(b"Ravi Staff", response.data)
        response = self.client.post(
            f"/issues/{issue_id}/status",
            data={
                "csrf_token": self.csrf_token("/admin"),
                "status": "in_progress",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Issue status updated.", response.data)
        self.assertIn(b"In Progress", response.data)
        self.assertEqual(self.issue_rows()[0]["status"], "in_progress")

    def test_invalid_issue_fields_are_rejected(self):
        invalid_cases = (
            {"title": "   "},
            {"title": "x" * 121},
            {"description": "   "},
            {"description": "x" * 2001},
            {"location": "   "},
            {"location": "x" * 161},
            {"category": "security"},
        )
        for invalid_values in invalid_cases:
            with self.subTest(invalid_values=invalid_values):
                response = self.submit_issue(**invalid_values)
                self.assertIn(b"Enter a title, description, location", response.data)
        self.assertEqual(len(self.issue_rows()), 0)

    def test_issue_submission_requires_login_and_csrf(self):
        self.client = self.app.test_client()
        response = self.client.post("/issues", data={})
        self.assertEqual(response.status_code, 302)

        self.login_as("student@campus.edu", "Student123!")
        response = self.client.post(
            "/issues",
            data={
                "title": "No token",
                "description": "Missing security token",
                "location": "Main Hall",
                "category": "other",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.issue_rows()), 0)

    def test_only_admin_can_change_issue_status_and_invalid_status_is_rejected(self):
        self.submit_issue()
        issue_id = self.issue_rows()[0]["id"]
        response = self.client.post(
            f"/issues/{issue_id}/status",
            data={"csrf_token": self.csrf_token(), "status": "resolved"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.issue_rows()[0]["status"], "open")

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/issues/{issue_id}/status",
            data={"csrf_token": self.csrf_token("/admin"), "status": "deleted"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.issue_rows()[0]["status"], "open")

    def test_issue_status_update_requires_csrf(self):
        self.submit_issue()
        issue_id = self.issue_rows()[0]["id"]
        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/issues/{issue_id}/status",
            data={"status": "resolved"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.issue_rows()[0]["status"], "open")

    def test_admin_can_approve_booking_and_owner_sees_updated_status(self):
        self.submit_booking(purpose="Approval workflow booking")
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings WHERE purpose = ?",
                ("Approval workflow booking",),
            ).fetchone()["id"]

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/admin/bookings/{booking_id}/approve",
            data={"csrf_token": self.csrf_token("/admin")},
            follow_redirects=True,
        )
        self.assertIn(b"Booking approved.", response.data)
        self.assertEqual(self.booking_status(booking_id), "approved")

        self.client = self.app.test_client()
        self.login_as("student@campus.edu", "Student123!")
        response = self.client.get("/dashboard")
        self.assertIn(b"Approved", response.data)
        self.assertIn(b"Approval workflow booking", response.data)

    def test_admin_can_reject_booking_and_owner_sees_updated_status(self):
        self.submit_booking(purpose="Rejected workflow booking")
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings WHERE purpose = ?",
                ("Rejected workflow booking",),
            ).fetchone()["id"]

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/admin/bookings/{booking_id}/reject",
            data={"csrf_token": self.csrf_token("/admin")},
            follow_redirects=True,
        )
        self.assertIn(b"Booking rejected.", response.data)
        self.assertEqual(self.booking_status(booking_id), "rejected")

        self.client = self.app.test_client()
        self.login_as("student@campus.edu", "Student123!")
        response = self.client.get("/dashboard")
        self.assertIn(b"Rejected", response.data)

    def test_non_admin_cannot_approve_or_reject_bookings(self):
        self.submit_booking()
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings"
            ).fetchone()["id"]

        for action in ("approve", "reject"):
            with self.subTest(action=action):
                response = self.client.post(
                    f"/admin/bookings/{booking_id}/{action}",
                    data={"csrf_token": self.csrf_token()},
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.booking_status(booking_id), "pending")

    def test_unauthenticated_users_cannot_use_admin_actions(self):
        self.submit_issue()
        issue_id = self.issue_rows()[0]["id"]
        self.submit_booking()
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings"
            ).fetchone()["id"]

        self.client = self.app.test_client()
        responses = (
            self.client.post(f"/issues/{issue_id}/status", data={"status": "resolved"}),
            self.client.post(f"/admin/bookings/{booking_id}/approve", data={}),
            self.client.post(f"/admin/bookings/{booking_id}/reject", data={}),
        )
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertEqual(self.issue_rows()[0]["status"], "open")
        self.assertEqual(self.booking_status(booking_id), "pending")

    def test_booking_admin_actions_require_csrf(self):
        self.submit_booking()
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings"
            ).fetchone()["id"]
        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")

        for action in ("approve", "reject"):
            with self.subTest(action=action):
                response = self.client.post(
                    f"/admin/bookings/{booking_id}/{action}",
                    data={},
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.booking_status(booking_id), "pending")

    def test_approval_rechecks_availability_before_changing_status(self):
        self.submit_booking(purpose="Pending booking")
        with get_connection(self.database_path) as connection:
            pending_id = connection.execute(
                "SELECT id FROM bookings WHERE purpose = 'Pending booking'"
            ).fetchone()["id"]
            staff_id = connection.execute(
                "SELECT id FROM users WHERE email = 'staff@campus.edu'"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO bookings
                    (user_id, resource_id, purpose, booking_date, start_time,
                     end_time, status, created_at, updated_at)
                VALUES (?, 1, 'Conflicting booking', ?, '09:30', '10:30',
                        'approved', '2098-01-01T00:00:00', '2098-01-01T00:00:00')
                """,
                (staff_id, self.booking_date),
            )

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/admin/bookings/{pending_id}/approve",
            data={"csrf_token": self.csrf_token("/admin")},
            follow_redirects=True,
        )
        self.assertIn(b"conflicts with another active booking", response.data)
        self.assertEqual(self.booking_status(pending_id), "pending")

    def test_database_initialization_migrates_legacy_issue_table(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy.db")
        connection = sqlite3.connect(legacy_path)
        try:
            connection.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO users
                    (id, name, email, role, password_hash, created_at)
                VALUES (1, 'Asha Student', 'student@campus.edu', 'student',
                        'existing-hash', '2098-01-01T00:00:00')
                """
            )
            connection.execute(
                """
                CREATE TABLE facility_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    resource_id INTEGER,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO facility_issues
                    (id, user_id, category, description, priority,
                     status, created_at, updated_at)
                VALUES (7, 1, 'cleaning', 'Legacy report', 'medium',
                        'open', '2098-01-01T00:00:00', '2098-01-01T00:00:00')
                """
            )
            connection.commit()
        finally:
            connection.close()

        init_db(legacy_path)
        with get_connection(legacy_path) as connection:
            rows = connection.execute(
                "SELECT id, title, location, description FROM facility_issues"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 7)
        self.assertEqual(rows[0]["title"], "Facility issue")
        self.assertEqual(rows[0]["location"], "Not specified")
        self.assertEqual(rows[0]["description"], "Legacy report")


if __name__ == "__main__":
    unittest.main()
