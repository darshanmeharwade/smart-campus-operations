"""Tests for the admin-only campus analytics dashboard."""

import os
import re
import tempfile
import unittest
from pathlib import Path

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-analytics-import-")
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


class AdminAnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="smart-campus-analytics-test-")
        self.database_path = str(Path(self.temp_dir.name) / "analytics.db")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "SECRET_KEY": "test-only-analytics-session-key",
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

    def insert_booking(self, user_id, resource_id, status, booking_date):
        with get_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO bookings
                    (user_id, resource_id, purpose, booking_date, start_time,
                     end_time, status, created_at, updated_at)
                VALUES (?, ?, 'Analytics test', ?, '09:00', '10:00', ?,
                        '2099-01-01T00:00:00', '2099-01-01T00:00:00')
                """,
                (user_id, resource_id, booking_date, status),
            )

    def insert_issue(self, user_id, category, status, created_at):
        with get_connection(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO facility_issues
                    (user_id, title, category, description, location, priority,
                     status, created_at, updated_at)
                VALUES (?, 'Analytics report', ?, 'Test issue', 'Test location',
                        'medium', ?, ?, ?)
                """,
                (user_id, category, status, created_at, created_at),
            )

    def insert_resource(self, name):
        with get_connection(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO resources
                    (name, resource_type, location, capacity, is_active, created_at)
                VALUES (?, 'room', 'Analytics test location', 10, 1,
                        '2099-01-01T00:00:00')
                """,
                (name,),
            )
            return cursor.lastrowid

    def facility_rankings(self, response):
        html = response.data.decode()
        section = re.search(
            r'<section class="analytics-panel" aria-labelledby="facility-analytics-title">(.*?)</section>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(section)
        return [
            (name, int(count))
            for name, count in re.findall(
                r"<tr>\s*<td>(.*?)</td>\s*<td>(\d+)</td>\s*</tr>",
                section.group(1),
                re.DOTALL,
            )
        ]

    def setUp_data_ids(self):
        with get_connection(self.database_path) as connection:
            student_id = connection.execute(
                "SELECT id FROM users WHERE email = 'student@campus.edu'"
            ).fetchone()["id"]
            staff_id = connection.execute(
                "SELECT id FROM users WHERE email = 'staff@campus.edu'"
            ).fetchone()["id"]
            resource_ids = {
                row["name"]: row["id"]
                for row in connection.execute(
                    "SELECT id, name FROM resources"
                ).fetchall()
            }
        return student_id, staff_id, resource_ids

    def test_admin_can_access_analytics_and_dashboard_is_not_shown_to_others(self):
        self.login_as("admin@campus.edu", "Admin123!")
        admin_response = self.client.get("/admin")
        self.assertEqual(admin_response.status_code, 200)
        self.assertIn(b"Operations analytics", admin_response.data)
        self.assertIn(b"Total bookings", admin_response.data)

        self.client = self.app.test_client()
        self.login_as("student@campus.edu", "Student123!")
        self.assertEqual(self.client.get("/admin").status_code, 403)
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b"Operations analytics", dashboard.data)

    def test_empty_database_metrics_render_zero_counts_and_empty_states(self):
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<strong>0</strong>", response.data)
        self.assertIn(b"Pending</span><span>0</span>", response.data)
        self.assertIn(b"Approved</span><span>0</span>", response.data)
        self.assertIn(b"Open</span><span>0</span>", response.data)
        self.assertIn(b"No approved bookings for this period.", response.data)
        self.assertIn(b"<td>Electrical</td><td>0</td>", response.data)

    def test_metrics_and_date_filter_use_database_records(self):
        student_id, staff_id, resource_ids = self.setUp_data_ids()
        selected_date = "2099-02-10"
        other_date = "2099-02-11"
        self.insert_booking(student_id, resource_ids["Lecture Hall 1"], "approved", selected_date)
        self.insert_booking(staff_id, resource_ids["Lecture Hall 1"], "approved", selected_date)
        self.insert_booking(staff_id, resource_ids["Seminar Room 204"], "approved", selected_date)
        self.insert_booking(student_id, resource_ids["Seminar Room 204"], "pending", selected_date)
        self.insert_booking(student_id, resource_ids["Computer Lab 3"], "rejected", other_date)

        self.insert_issue(student_id, "electrical", "open", f"{selected_date}T09:00:00+05:30")
        self.insert_issue(staff_id, "electrical", "resolved", f"{selected_date}T10:00:00+05:30")
        self.insert_issue(student_id, "cleaning", "in_progress", f"{selected_date}T11:00:00+05:30")
        self.insert_issue(staff_id, "network", "open", f"{other_date}T09:00:00+05:30")

        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<strong>5</strong>", response.data)
        self.assertIn(b"<strong>4</strong>", response.data)
        self.assertIn(b"Pending</span><span>1</span>", response.data)
        self.assertIn(b"Approved</span><span>3</span>", response.data)
        self.assertIn(b"Rejected</span><span>1</span>", response.data)
        self.assertIn(b"Open</span><span>2</span>", response.data)
        self.assertIn(b"In Progress</span><span>1</span>", response.data)
        self.assertIn(b"Resolved</span><span>1</span>", response.data)

        self.assertEqual(
            self.facility_rankings(response),
            [("Lecture Hall 1", 2), ("Seminar Room 204", 1)],
        )
        self.assertIn(b"<td>Electrical</td><td>2</td>", response.data)
        self.assertIn(b"<td>Cleaning</td><td>1</td>", response.data)
        self.assertIn(b"<td>Network</td><td>1</td>", response.data)

        filtered = self.client.get(f"/admin?analytics_date={selected_date}")
        self.assertEqual(filtered.status_code, 200)
        self.assertIn(b"<strong>4</strong>", filtered.data)
        self.assertIn(b"<strong>3</strong>", filtered.data)
        self.assertIn(b"Approved</span><span>3</span>", filtered.data)
        self.assertIn(b"Open</span><span>1</span>", filtered.data)
        self.assertIn(b"<td>Electrical</td><td>2</td>", filtered.data)
        self.assertIn(b"<td>Network</td><td>0</td>", filtered.data)
        self.assertEqual(
            self.facility_rankings(filtered),
            [("Lecture Hall 1", 2), ("Seminar Room 204", 1)],
        )

    def test_facility_rankings_break_ties_by_name_and_return_at_most_five(self):
        student_id, _, resource_ids = self.setUp_data_ids()
        names = [
            "Lecture Hall 1",
            "Seminar Room 204",
            "Computer Lab 3",
            "Portable PA System",
            "Projector Kit A",
            "A Studio",
            "Z Workshop",
        ]
        for name in names:
            resource_id = resource_ids.get(name)
            if resource_id is None:
                resource_id = self.insert_resource(name)
            self.insert_booking(
                student_id,
                resource_id,
                "approved",
                "2099-02-10",
            )

        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.facility_rankings(response),
            [
                ("A Studio", 1),
                ("Computer Lab 3", 1),
                ("Lecture Hall 1", 1),
                ("Portable PA System", 1),
                ("Projector Kit A", 1),
            ],
        )
        self.assertEqual(len(self.facility_rankings(response)), 5)

    def test_valid_iso_analytics_date_is_accepted(self):
        self.login_as("admin@campus.edu", "Admin123!")
        response = self.client.get(
            "/admin",
            query_string={"analytics_date": "2099-02-10"},
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_analytics_dates_are_rejected(self):
        self.login_as("admin@campus.edu", "Admin123!")
        for invalid_date in (
            "not-a-date",
            "2026-02-30",
            "20260210",
            "2026-2-10",
            " 2099-02-10 ",
            "2099-02-10 ",
            " 2099-02-10",
        ):
            with self.subTest(invalid_date=invalid_date):
                response = self.client.get(
                    "/admin",
                    query_string={"analytics_date": invalid_date},
                )
                self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
