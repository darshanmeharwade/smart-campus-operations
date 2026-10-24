"""Tests for local and Vercel SQLite path selection."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(
    prefix="smart-campus-database-path-import-"
)
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
_PREVIOUS_VERCEL = os.environ.get("VERCEL")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
os.environ.pop("VERCEL", None)
try:
    from app import create_app
    from db import BASE_DIR, default_database_path, get_connection, init_db
finally:
    if _PREVIOUS_DATABASE_PATH is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = _PREVIOUS_DATABASE_PATH
    if _PREVIOUS_VERCEL is None:
        os.environ.pop("VERCEL", None)
    else:
        os.environ["VERCEL"] = _PREVIOUS_VERCEL


class DatabasePathTests(unittest.TestCase):
    def test_default_path_is_local_instance_directory_outside_vercel(self):
        with patch.dict(os.environ, {"VERCEL": "", "DATABASE_PATH": ""}):
            self.assertEqual(
                default_database_path(),
                str(BASE_DIR / "instance" / "campus.db"),
            )

    def test_default_path_uses_tmp_on_vercel(self):
        with patch.dict(os.environ, {"VERCEL": "1", "DATABASE_PATH": ""}):
            self.assertEqual(default_database_path(), "/tmp/campus.db")

    def test_explicit_database_path_overrides_platform_default(self):
        custom_path = "custom/storage/campus.db"
        with patch.dict(
            os.environ,
            {"VERCEL": "1", "DATABASE_PATH": custom_path},
        ):
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-only-secret",
                }
            )
        self.assertEqual(app.config["DATABASE"], custom_path)

    def test_vercel_app_selects_tmp_path_without_writing_during_path_check(self):
        with patch.dict(
            os.environ,
            {
                "VERCEL": "1",
                "DATABASE_PATH": "",
                "APP_ENV": "development",
                "SECRET_KEY": "",
            },
        ), patch("app.init_db") as initialize:
            app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-only-secret",
                }
            )

        self.assertEqual(app.config["DATABASE"], "/tmp/campus.db")
        initialize.assert_called_once_with("/tmp/campus.db", seed_demo_users=True)

    def test_init_db_creates_and_initializes_database_under_writable_temp_parent(self):
        with tempfile.TemporaryDirectory(
            prefix="smart-campus-database-init-"
        ) as temporary_directory:
            database_path = Path(temporary_directory) / "nested" / "campus.db"

            init_db(str(database_path), seed_demo_users=False)

            self.assertTrue(database_path.is_file())
            with get_connection(str(database_path)) as connection:
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                resources = connection.execute(
                    "SELECT count(*) AS count FROM resources"
                ).fetchone()["count"]

            self.assertTrue(
                {"users", "resources", "bookings", "facility_issues", "audit_logs"}
                .issubset(tables)
            )
            self.assertEqual(resources, 5)


if __name__ == "__main__":
    unittest.main()
