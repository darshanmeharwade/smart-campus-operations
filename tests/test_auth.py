"""Automated tests for Stage 2 authentication and role access."""

import os
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-import-test-")
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
try:
    from app import create_app
    from db import get_connection
finally:
    if _PREVIOUS_DATABASE_PATH is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = _PREVIOUS_DATABASE_PATH


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "test-campus.db")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "SECRET_KEY": "test-only-session-key",
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        if match is None:
            self.fail("Login form did not include its CSRF token.")
        return match.group(1).decode()

    def login(self, email, password):
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": self.csrf_token(),
            },
            follow_redirects=False,
        )

    def registration_csrf_token(self):
        response = self.client.get("/register")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            self.fail("Registration form did not include its CSRF token.")
        return match.group(1).decode()

    def register(self, name, email, password, **extra):
        data = {
            "name": name,
            "email": email,
            "password": password,
            "csrf_token": self.registration_csrf_token(),
            **extra,
        }
        return self.client.post(
            "/register",
            data=data,
            follow_redirects=False,
        )

    def test_student_can_log_in_with_demo_password(self):
        response = self.login("student@campus.edu", "Student123!")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Hello, Asha Student.", dashboard.data)
        self.assertIn(b"Student", dashboard.data)

    def test_public_login_page_has_only_credentials_and_no_demo_accounts(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="email"', response.data)
        self.assertIn(b'id="email"\n                        name="email"\n                        type="text"', response.data)
        self.assertIn(b'name="password"', response.data)
        self.assertIn(b">Login</button>", response.data)
        self.assertIn(b"Create Student Account", response.data)
        self.assertNotIn(b"name=\"role\"", response.data)
        for credential in (
            b"student@campus.edu",
            b"staff@campus.edu",
            b"admin@campus.edu",
            b"admin@123",
            b"Student123!",
            b"Staff123!",
            b"Admin123",
        ):
            self.assertNotIn(credential, response.data)

    def test_student_registration_hashes_password_and_assigns_student_role(self):
        signup_page = self.client.get("/register")
        self.assertEqual(signup_page.status_code, 200)
        self.assertIn(b'name="name"', signup_page.data)
        self.assertIn(b'name="email"', signup_page.data)
        self.assertIn(b'id="email"\n                        name="email"\n                        type="text"', signup_page.data)
        self.assertIn(b'name="password"', signup_page.data)
        self.assertNotIn(b'name="role"', signup_page.data)

        response = self.register(
            "New Student",
            "New.Student@campus.edu",
            "SafePassword123",
            role="admin",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")

        with get_connection(self.database_path) as connection:
            user = connection.execute(
                "SELECT name, email, role, password_hash FROM users "
                "WHERE email = ?",
                ("new.student@campus.edu",),
            ).fetchone()
        self.assertIsNotNone(user)
        self.assertEqual(user["name"], "New Student")
        self.assertEqual(user["role"], "student")
        self.assertNotEqual(user["password_hash"], "SafePassword123")
        self.assertTrue(check_password_hash(user["password_hash"], "SafePassword123"))

    def test_registration_then_login_allows_student_to_book_facility(self):
        self.register("Campus User", "campus.user@campus.edu", "SafePassword123")
        response = self.login("campus.user@campus.edu", "SafePassword123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

        dashboard = self.client.get("/dashboard")
        token_match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            dashboard.data,
        )
        self.assertIsNotNone(token_match)
        booking = self.client.post(
            "/bookings",
            data={
                "csrf_token": token_match.group(1).decode(),
                "resource_id": "1",
                "booking_date": "2099-01-15",
                "start_time": "09:00",
                "end_time": "10:00",
                "purpose": "Student project meeting",
            },
        )
        self.assertEqual(booking.status_code, 302)
        with get_connection(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT users.email, users.role, bookings.status
                FROM bookings JOIN users ON users.id = bookings.user_id
                WHERE users.email = ?
                """,
                ("campus.user@campus.edu",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "student")
        self.assertEqual(row["status"], "pending")

    def test_non_email_identifiers_register_and_log_in_after_normalization(self):
        for identifier in ("darshan123", "student1", "abc@gmail.com"):
            with self.subTest(identifier=identifier):
                self.client = self.app.test_client()
                response = self.register(
                    "Campus Student",
                    f"  {identifier}  ",
                    "SafePassword123",
                )
                self.assertEqual(response.status_code, 302)
                response = self.login(
                    f"  {identifier.upper()}  ",
                    "SafePassword123",
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], "/dashboard")
                self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_registration_rejects_duplicate_emails_case_insensitively(self):
        self.register("First User", "same-user", "SafePassword123")
        response = self.register("Second User", " SAME-USER ", "SafePassword123")
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"already exists", response.data)

    def test_registration_rejects_invalid_fields_and_short_password(self):
        invalid_inputs = (
            ("", "valid@campus.edu", "SafePassword123"),
            ("Student", "   ", "SafePassword123"),
            ("Student", "valid@campus.edu", "short"),
            (" " * 101, "valid@campus.edu", "SafePassword123"),
        )
        for name, email, password in invalid_inputs:
            with self.subTest(name=name[:10], email=email, password=password):
                response = self.register(name, email, password)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"at least 8 characters", response.data)

    def test_registration_requires_csrf_token(self):
        response = self.client.post(
            "/register",
            data={
                "name": "No CSRF",
                "email": "nocsrf@campus.edu",
                "password": "SafePassword123",
            },
        )
        self.assertEqual(response.status_code, 400)
        with get_connection(self.database_path) as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE email = ?",
                ("nocsrf@campus.edu",),
            ).fetchone()
        self.assertIsNone(user)

    def test_registration_role_parameters_cannot_create_admin(self):
        response = self.client.post(
            "/register?role=admin",
            data={
                "name": "Attempted Admin",
                "email": "attempted.admin@campus.edu",
                "password": "SafePassword123",
                "role": "admin",
                "csrf_token": self.registration_csrf_token(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.get("/register?role=admin").status_code,
            200,
        )
        response = self.login(
            "attempted.admin@campus.edu",
            "SafePassword123",
        )
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_reserved_admin_identifier_cannot_be_registered_as_student(self):
        response = self.register("Impersonator", " ADMIN@123 ", "SafePassword123")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"available identifier", response.data)
        with get_connection(self.database_path) as connection:
            account = connection.execute(
                "SELECT role FROM users WHERE email = ?",
                ("admin@123",),
            ).fetchone()
        self.assertEqual(account["role"], "admin")

    def test_submitted_role_cannot_elevate_user_permissions(self):
        response = self.client.post(
            "/login",
            data={
                "email": "student@campus.edu",
                "password": "Student123!",
                "role": "admin",
                "csrf_token": self.csrf_token(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_incorrect_password_shows_generic_error(self):
        response = self.login("student@campus.edu", "wrong-password")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Email or password is incorrect.", response.data)

    def test_unknown_database_user_cannot_log_in(self):
        response = self.login("unknown@campus.edu", "some-password")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Email or password is incorrect.", response.data)

    def test_logout_clears_authenticated_session(self):
        login_response = self.login("student2@campus.edu", "Student123!")
        cookie_header = login_response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)
        self.assertIn("Expires=", cookie_header)

        token = self.client.get("/dashboard").data
        match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            token,
        )
        if match is None:
            self.fail("Dashboard form did not include its CSRF token.")

        response = self.client.post(
            "/logout",
            data={"csrf_token": match.group(1).decode()},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login")
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_logout_requires_csrf_token(self):
        self.login("student2@campus.edu", "Student123!")
        response = self.client.post("/logout", data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_unauthenticated_users_cannot_access_protected_pages(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_students_are_forbidden_from_admin_route(self):
        for email, password in (
            ("student@campus.edu", "Student123!"),
            ("student2@campus.edu", "Student123!"),
        ):
            with self.subTest(email=email):
                self.client = self.app.test_client()
                self.login(email, password)
                self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_admin_can_access_admin_route(self):
        response = self.login("admin@123", "Admin123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")
        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"ADMINISTRATOR WORKSPACE", admin_page.data)

    def test_login_rejects_missing_or_invalid_form_values(self):
        response = self.client.post(
            "/login",
            data={"email": "   ", "password": "", "csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Enter an identifier and password.", response.data)

    def test_login_rejects_missing_csrf_token(self):
        response = self.client.post(
            "/login",
            data={"email": "student@campus.edu", "password": "Student123!"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
