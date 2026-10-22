"""Tests for local issue triage suggestions and admin decisions."""

import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-triage-import-")
_IMPORT_DATABASE_PATH = str(Path(_IMPORT_DATABASE_DIR.name) / "app-import.db")
_PREVIOUS_DATABASE_PATH = os.environ.get("DATABASE_PATH")
os.environ["DATABASE_PATH"] = _IMPORT_DATABASE_PATH
try:
    from app import create_app
    from db import get_connection, init_db
    from issue_triage import recommend_issue_triage
finally:
    if _PREVIOUS_DATABASE_PATH is None:
        os.environ.pop("DATABASE_PATH", None)
    else:
        os.environ["DATABASE_PATH"] = _PREVIOUS_DATABASE_PATH


class IssueTriageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="smart-campus-triage-test-")
        self.database_path = str(Path(self.temp_dir.name) / "triage.db")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "SECRET_KEY": "test-only-triage-session-key",
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()

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
        response = self.client.post("/issues", data=data, follow_redirects=True)
        self.assertIn(b"Facility issue report submitted.", response.data)
        with get_connection(self.database_path) as connection:
            return connection.execute(
                "SELECT * FROM facility_issues ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def test_category_and_priority_suggestions_use_report_text(self):
        plumbing = recommend_issue_triage(
            {
                "title": "Water leak in washroom",
                "description": "Water is flooding from a broken pipe.",
                "category": "cleaning",
            }
        )
        self.assertEqual(plumbing["recommended_category"], "plumbing")
        self.assertEqual(plumbing["recommended_priority"], "High")
        self.assertIn("leak", plumbing["triage_reason"])

        electrical = recommend_issue_triage(
            {
                "title": "Smoke near electrical panel",
                "description": "There is smoke coming from the electrical panel.",
                "category": "other",
            }
        )
        self.assertEqual(electrical["recommended_category"], "electrical")
        self.assertEqual(electrical["recommended_priority"], "Critical")
        self.assertIn("smoke", electrical["triage_reason"])

        low_priority = recommend_issue_triage(
            {
                "title": "Minor Wi-Fi issue",
                "description": "A small connection problem in one room.",
                "category": "other",
            }
        )
        self.assertEqual(low_priority["recommended_category"], "network")
        self.assertEqual(low_priority["recommended_priority"], "Low")

    def test_medium_default_and_unknown_or_ambiguous_categories_are_safe(self):
        empty_report = recommend_issue_triage(
            {"title": " ", "description": "", "category": "unknown"}
        )
        self.assertEqual(empty_report["recommended_category"], "other")
        self.assertEqual(empty_report["recommended_priority"], "Medium")
        self.assertIn("Medium is the cautious default", empty_report["triage_reason"])

        ambiguous_report = recommend_issue_triage(
            {
                "title": "Please check this",
                "description": "Something seems unusual.",
                "category": "furniture",
            }
        )
        self.assertEqual(ambiguous_report["recommended_category"], "furniture")
        self.assertEqual(ambiguous_report["recommended_priority"], "Medium")
        self.assertIn("submitted category is retained", ambiguous_report["triage_reason"])

    def test_only_admins_see_suggestions_and_can_save_separate_decisions(self):
        self.login_as("student@campus.edu", "Student123!")
        issue = self.submit_issue()
        self.assertEqual(issue["category"], "electrical")
        self.assertIsNone(issue["admin_category"])
        self.assertIsNone(issue["admin_priority"])

        self.client = self.app.test_client()
        unauthenticated = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={
                "admin_category": "plumbing",
                "admin_priority": "high",
            },
        )
        self.assertEqual(unauthenticated.status_code, 302)

        self.client = self.app.test_client()
        self.login_as("staff@campus.edu", "Staff123!")
        dashboard = self.client.get("/dashboard")
        self.assertNotIn(b"Triage suggestion", dashboard.data)
        denied = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={
                "csrf_token": self.csrf_token(),
                "admin_category": "plumbing",
                "admin_priority": "high",
            },
        )
        self.assertEqual(denied.status_code, 403)

        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        dashboard = self.client.get("/admin")
        self.assertIn(b"Triage suggestion", dashboard.data)
        self.assertIn(b"Rule-based suggestion; review before deciding.", dashboard.data)

        no_csrf = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={"admin_category": "plumbing", "admin_priority": "high"},
        )
        self.assertEqual(no_csrf.status_code, 400)
        invalid = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={
                "csrf_token": self.csrf_token("/admin"),
                "admin_category": "unknown",
                "admin_priority": "urgent",
            },
        )
        self.assertEqual(invalid.status_code, 400)

        saved = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={
                "csrf_token": self.csrf_token("/admin"),
                "admin_category": "plumbing",
                "admin_priority": "critical",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Admin triage decision saved.", saved.data)
        with get_connection(self.database_path) as connection:
            stored = connection.execute(
                """
                SELECT title, description, category, admin_category,
                       admin_priority, status
                FROM facility_issues WHERE id = ?
                """,
                (issue["id"],),
            ).fetchone()
        self.assertEqual(stored["title"], "Broken projector")
        self.assertEqual(stored["description"], "The projector does not power on.")
        self.assertEqual(stored["category"], "electrical")
        self.assertEqual(stored["admin_category"], "plumbing")
        self.assertEqual(stored["admin_priority"], "critical")
        self.assertEqual(stored["status"], "open")

    def test_issue_status_workflow_still_works_with_triage(self):
        self.login_as("student@campus.edu", "Student123!")
        issue = self.submit_issue()
        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/issues/{issue['id']}/status",
            data={
                "csrf_token": self.csrf_token("/admin"),
                "status": "in_progress",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Issue status updated.", response.data)
        with get_connection(self.database_path) as connection:
            status = connection.execute(
                "SELECT status FROM facility_issues WHERE id = ?",
                (issue["id"],),
            ).fetchone()["status"]
        self.assertEqual(status, "in_progress")

    def test_repeated_migration_preserves_existing_issue_data(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy-triage.db")
        connection = sqlite3.connect(legacy_path)
        try:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                INSERT INTO users
                    VALUES (1, 'Legacy Reporter', 'legacy@campus.edu',
                            'student', 'hash', '2099-01-01T00:00:00');
                CREATE TABLE facility_issues (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO facility_issues
                    (id, user_id, category, description, priority, status,
                     created_at, updated_at)
                VALUES (7, 1, 'network', 'Legacy Wi-Fi outage', 'high',
                        'open', '2099-01-01T00:00:00', '2099-01-01T00:00:00');
                """
            )
            connection.commit()
        finally:
            connection.close()

        init_db(legacy_path)
        init_db(legacy_path)
        with get_connection(legacy_path) as connection:
            row = connection.execute(
                """
                SELECT id, category, description, admin_category, admin_priority
                FROM facility_issues WHERE id = 7
                """
            ).fetchone()
            columns = {
                item["name"]
                for item in connection.execute("PRAGMA table_info(facility_issues)")
            }
        self.assertEqual(row["category"], "network")
        self.assertEqual(row["description"], "Legacy Wi-Fi outage")
        self.assertIsNone(row["admin_category"])
        self.assertIsNone(row["admin_priority"])
        self.assertTrue({"admin_category", "admin_priority"} <= columns)

    def test_reinitialization_preserves_issue_and_saved_admin_decision(self):
        self.login_as("student@campus.edu", "Student123!")
        issue = self.submit_issue(
            title="Water leak in seminar room",
            description="A pipe is leaking under the sink.",
            location="Seminar Room 204",
            category="plumbing",
        )
        self.client = self.app.test_client()
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.post(
            f"/issues/{issue['id']}/triage",
            data={
                "csrf_token": self.csrf_token("/admin"),
                "admin_category": "plumbing",
                "admin_priority": "high",
            },
        )
        self.assertEqual(response.status_code, 302)

        with get_connection(self.database_path) as connection:
            before = tuple(
                connection.execute(
                    """
                    SELECT title, description, location, category, priority,
                           status, admin_category, admin_priority, created_at, updated_at
                    FROM facility_issues WHERE id = ?
                    """,
                    (issue["id"],),
                ).fetchone()
            )
        self.assertEqual(
            before[:8],
            (
                "Water leak in seminar room",
                "A pipe is leaking under the sink.",
                "Seminar Room 204",
                "plumbing",
                "medium",
                "open",
                "plumbing",
                "high",
            ),
        )

        init_db(self.database_path)

        with get_connection(self.database_path) as connection:
            after = tuple(
                connection.execute(
                    """
                    SELECT title, description, location, category, priority,
                           status, admin_category, admin_priority, created_at, updated_at
                    FROM facility_issues WHERE id = ?
                    """,
                    (issue["id"],),
                ).fetchone()
            )
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
