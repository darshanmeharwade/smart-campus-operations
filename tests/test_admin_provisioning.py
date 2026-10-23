"""Tests for the one-time administrator provisioning command."""

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import check_password_hash, generate_password_hash

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(
    prefix="smart-campus-admin-provisioning-import-"
)
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
try:
    from app import create_app
    from db import get_connection, init_db
    from provision_admin import main
finally:
    if _PREVIOUS_DATABASE_PATH is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = _PREVIOUS_DATABASE_PATH


class AdminProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix="smart-campus-admin-provisioning-test-"
        )
        self.database_path = str(Path(self.temp_dir.name) / "campus.db")
        init_db(self.database_path, seed_demo_users=False)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_provisioner(self, name, email, password, confirmation=None):
        output = io.StringIO()
        errors = io.StringIO()
        entered_passwords = [password, confirmation or password]
        with (
            patch("builtins.input", return_value=name),
            patch(
                "getpass.getpass",
                side_effect=[email, *entered_passwords],
            ),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            result = main(["--database", self.database_path])
        return result, output.getvalue(), errors.getvalue()

    def csrf_token(self, client, page):
        response = client.get(page)
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            self.fail(f"{page} did not include its CSRF token.")
        return match.group(1).decode()

    def test_provisioned_admin_can_log_in_and_public_signup_is_student_only(self):
        password = "A-Strong-Admin-Password-923!"
        result, output, errors = self.run_provisioner(
            "Campus Administrator",
            "admin@123",
            password,
        )
        self.assertEqual(result, 0, errors)
        self.assertNotIn(password, output)
        self.assertNotIn(password, errors)

        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "SECRET_KEY": "stable-production-test-secret",
                "DATABASE_PATH": self.database_path,
            },
        ):
            app = create_app(
                {
                    "TESTING": True,
                    "DATABASE": self.database_path,
                }
            )

        admin_client = app.test_client()
        response = admin_client.post(
            "/login",
            data={
                "email": "ADMIN@123",
                "password": password,
                "csrf_token": self.csrf_token(admin_client, "/login"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")
        self.assertEqual(admin_client.get("/admin").status_code, 200)

        student_client = app.test_client()
        response = student_client.post(
            "/register",
            data={
                "name": "Public Student",
                "email": "public.student@campus.edu",
                "password": "Student-Password-123!",
                "role": "admin",
                "csrf_token": self.csrf_token(student_client, "/register"),
            },
        )
        self.assertEqual(response.status_code, 302)
        response = student_client.post(
            "/login",
            data={
                "email": "public.student@campus.edu",
                "password": "Student-Password-123!",
                "csrf_token": self.csrf_token(student_client, "/login"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(student_client.get("/admin").status_code, 403)

        self.assertEqual(student_client.get("/dashboard").status_code, 200)
        student_csrf = self.csrf_token(student_client, "/dashboard")
        response = student_client.post(
            "/bookings",
            data={
                "csrf_token": student_csrf,
                "resource_id": "1",
                "booking_date": "2099-01-15",
                "start_time": "09:00",
                "end_time": "10:00",
                "purpose": "Provisioned admin review",
            },
        )
        self.assertEqual(response.status_code, 302)
        with get_connection(self.database_path) as connection:
            booking_id = connection.execute(
                "SELECT id FROM bookings WHERE purpose = ?",
                ("Provisioned admin review",),
            ).fetchone()["id"]

        admin_dashboard = admin_client.get("/admin")
        self.assertIn(b"Pending booking requests", admin_dashboard.data)
        self.assertIn(b"Public Student", admin_dashboard.data)
        self.assertIn(b"Lecture Hall 1", admin_dashboard.data)
        self.assertIn(b"Provisioned admin review", admin_dashboard.data)
        self.assertIn(b"Approve</button>", admin_dashboard.data)
        self.assertIn(b"Reject</button>", admin_dashboard.data)
        admin_token = self.csrf_token(admin_client, "/admin")
        response = admin_client.post(
            f"/admin/bookings/{booking_id}/approve",
            data={"csrf_token": admin_token},
        )
        self.assertEqual(response.status_code, 302)
        with get_connection(self.database_path) as connection:
            status = connection.execute(
                "SELECT status FROM bookings WHERE id = ?",
                (booking_id,),
            ).fetchone()["status"]
        self.assertEqual(status, "approved")

        with get_connection(self.database_path) as connection:
            accounts = connection.execute(
                "SELECT email, role FROM users ORDER BY email"
            ).fetchall()
        self.assertEqual(
            [(row["email"], row["role"]) for row in accounts],
            [
                ("admin@123", "admin"),
                ("public.student@campus.edu", "student"),
            ],
        )

    def test_duplicate_email_does_not_change_existing_account(self):
        existing_password_hash = generate_password_hash("Existing-Admin-Password-923!")
        with get_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO users (name, email, role, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "Existing Administrator",
                    "admin@123",
                    "admin",
                    existing_password_hash,
                    "2026-01-01T00:00:00+00:00",
                ),
            )

        result, output, errors = self.run_provisioner(
            "Replacement Admin",
            " ADMIN@123 ",
            "Different-Strong-Password-923!",
        )
        self.assertEqual(result, 1)
        self.assertIn("already exists", errors)
        self.assertNotIn("Different-Strong-Password-923!", output + errors)
        with get_connection(self.database_path) as connection:
            users = connection.execute(
                "SELECT name, role, password_hash FROM users "
                "WHERE email = ?",
                ("admin@123",),
            ).fetchall()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["name"], "Existing Administrator")
        self.assertEqual(users[0]["role"], "admin")
        self.assertEqual(users[0]["password_hash"], existing_password_hash)

    def test_password_confirmation_and_policy_are_enforced(self):
        result, _, errors = self.run_provisioner(
            "Campus Administrator",
            "admin@123",
            "Strong-Admin-Password-923!",
            confirmation="different-password",
        )
        self.assertEqual(result, 1)
        self.assertIn("do not match", errors)

        result, _, errors = self.run_provisioner(
            "Campus Administrator",
            "admin@123",
            "short",
        )
        self.assertEqual(result, 1)
        self.assertIn("between 12", errors)
        with get_connection(self.database_path) as connection:
            count = connection.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(count, 0)

    def test_existing_database_role_and_password_hash_are_verified(self):
        password = "A-Strong-Admin-Password-923!"
        result, _, errors = self.run_provisioner(
            "Campus Administrator",
            "admin@123",
            password,
        )
        self.assertEqual(result, 0, errors)
        with get_connection(self.database_path) as connection:
            account = connection.execute(
                "SELECT role, password_hash FROM users WHERE email = ?",
                ("admin@123",),
            ).fetchone()
        self.assertEqual(account["role"], "admin")
        self.assertNotEqual(account["password_hash"], password)
        self.assertTrue(check_password_hash(account["password_hash"], password))

    def test_reinitialization_converts_legacy_staff_and_other_admins_to_students(self):
        with get_connection(self.database_path) as connection:
            connection.executemany(
                """
                INSERT INTO users (name, email, role, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (
                        "Legacy Staff",
                        "legacy-staff",
                        "staff",
                        generate_password_hash("Legacy-Staff-Password-923!"),
                        "2026-01-01T00:00:00+00:00",
                    ),
                    (
                        "Old Administrator",
                        "old-admin",
                        "admin",
                        generate_password_hash("Old-Admin-Password-923!"),
                        "2026-01-01T00:00:00+00:00",
                    ),
                    (
                        "Provisioned Administrator",
                        "admin@123",
                        "admin",
                        generate_password_hash("Provisioned-Admin-Password-923!"),
                        "2026-01-01T00:00:00+00:00",
                    ),
                ),
            )
        init_db(self.database_path, seed_demo_users=False)
        with get_connection(self.database_path) as connection:
            users = connection.execute(
                "SELECT email, role FROM users ORDER BY email"
            ).fetchall()
        self.assertEqual(
            [(row["email"], row["role"]) for row in users],
            [
                ("admin@123", "admin"),
                ("legacy-staff", "student"),
                ("old-admin", "student"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
