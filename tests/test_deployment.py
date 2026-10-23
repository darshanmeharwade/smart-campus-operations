"""Tests for production deployment configuration and demo-account safeguards."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(
    prefix="smart-campus-deployment-import-"
)
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


class DeploymentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "deployment-test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def environment(self, **values):
        return patch.dict(
            os.environ,
            {
                "APP_ENV": "development",
                "SECRET_KEY": "",
                "RENDER": "",
                "SESSION_COOKIE_SECURE": "",
                "DEMO_USERS_ENABLED": "true",
                **values,
            },
        )

    def test_production_requires_environment_secret_key(self):
        with self.environment(
            APP_ENV="production",
            SECRET_KEY="",
            DATABASE_PATH=self.database_path,
        ):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY must be set"):
                create_app()

    def test_development_keeps_local_demo_accounts_and_http_cookies(self):
        with self.environment(DATABASE_PATH=self.database_path):
            app = create_app(
                {
                    "TESTING": True,
                    "DATABASE": self.database_path,
                    "SECRET_KEY": "test-only-secret",
                }
            )

        self.assertTrue(app.config["DEMO_USERS_ENABLED"])
        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        with get_connection(self.database_path) as connection:
            users = connection.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(users, 3)

    def test_production_forces_secure_cookies_and_blocks_existing_demo_login(self):
        with self.environment(DATABASE_PATH=self.database_path):
            create_app(
                {
                    "TESTING": True,
                    "DATABASE": self.database_path,
                    "SECRET_KEY": "local-test-secret",
                }
            )

        with self.environment(
            APP_ENV="production",
            SECRET_KEY="stable-production-test-secret",
            DATABASE_PATH=self.database_path,
            DEMO_USERS_ENABLED="true",
            SESSION_COOKIE_SECURE="false",
        ):
            app = create_app(
                {
                    "TESTING": True,
                    "DATABASE": self.database_path,
                }
            )

        with get_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (
                    generate_password_hash("Fresh-Student-Password-123!"),
                    "student@campus.edu",
                ),
            )

        self.assertFalse(app.config["DEMO_USERS_ENABLED"])
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

        client = app.test_client()
        login_page = client.get("/login")
        self.assertIn("Secure", login_page.headers.get("Set-Cookie", ""))
        csrf_match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            login_page.data,
        )
        self.assertIsNotNone(csrf_match)
        response = client.post(
            "/login",
            data={
                "email": "admin@123",
                "password": "Admin123",
                "csrf_token": csrf_match.group(1).decode(),
            },
        )
        self.assertEqual(response.status_code, 401)

        response = client.post(
            "/login",
            data={
                "email": "student@campus.edu",
                "password": "Fresh-Student-Password-123!",
                "csrf_token": csrf_match.group(1).decode(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        with get_connection(self.database_path) as connection:
            users = connection.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(users, 3)

    def test_database_path_can_be_set_to_external_location(self):
        with self.environment(DATABASE_PATH=self.database_path):
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-only-secret",
                }
            )

        self.assertEqual(app.config["DATABASE"], self.database_path)
        self.assertTrue(Path(self.database_path).is_file())


if __name__ == "__main__":
    unittest.main()
