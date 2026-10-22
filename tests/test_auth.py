"""Automated tests for Stage 2 authentication and role access."""

import os
import re
import tempfile
import unittest
from pathlib import Path

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-import-test-")
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
try:
    from app import create_app
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

    def test_student_can_log_in_with_demo_password(self):
        response = self.login("student@campus.edu", "Student123!")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")

        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Hello, Asha Student.", dashboard.data)
        self.assertIn(b"Student", dashboard.data)

    def test_incorrect_password_shows_generic_error(self):
        response = self.login("student@campus.edu", "wrong-password")
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Email or password is incorrect.", response.data)

    def test_logout_clears_authenticated_session(self):
        login_response = self.login("staff@campus.edu", "Staff123!")
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
        self.login("staff@campus.edu", "Staff123!")
        response = self.client.post("/logout", data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_unauthenticated_users_cannot_access_protected_pages(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_students_and_staff_are_forbidden_from_admin_route(self):
        for email, password in (
            ("student@campus.edu", "Student123!"),
            ("staff@campus.edu", "Staff123!"),
        ):
            with self.subTest(email=email):
                self.client = self.app.test_client()
                self.login(email, password)
                self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_admin_can_access_admin_route(self):
        response = self.login("admin@campus.edu", "Admin123!")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")
        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"ADMINISTRATOR WORKSPACE", admin_page.data)

    def test_login_rejects_missing_or_invalid_form_values(self):
        response = self.client.post(
            "/login",
            data={"email": "not-an-email", "password": "", "csrf_token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Enter a valid email address and password.", response.data)

    def test_login_rejects_missing_csrf_token(self):
        response = self.client.post(
            "/login",
            data={"email": "student@campus.edu", "password": "Student123!"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
