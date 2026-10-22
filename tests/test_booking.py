"""Tests for authenticated facility search and booking actions."""

import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_IMPORT_DATABASE_DIR = tempfile.TemporaryDirectory(prefix="smart-campus-booking-import-")
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

from db import get_connection


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="smart-campus-booking-test-")
        self.database_path = str(Path(self.temp_dir.name) / "bookings.db")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.database_path,
                "SECRET_KEY": "test-only-booking-session-key",
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.client = self.app.test_client()
        self.login_as("student@campus.edu", "Student123!")
        self.booking_date = "2099-01-15"

    def tearDown(self):
        self.temp_dir.cleanup()

    def get_csrf_token(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            self.fail("Dashboard form did not include its CSRF token.")
        return match.group(1).decode()

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

    def submit_booking(
        self,
        resource_id=1,
        booking_date=None,
        start_time="09:00",
        end_time="10:00",
        purpose="Project meeting",
        csrf_token=None,
    ):
        return self.client.post(
            "/bookings",
            data={
                "csrf_token": csrf_token or self.get_csrf_token(),
                "resource_id": str(resource_id),
                "booking_date": booking_date or self.booking_date,
                "start_time": start_time,
                "end_time": end_time,
                "purpose": purpose,
            },
            follow_redirects=True,
        )

    def stored_bookings(self):
        with get_connection(self.database_path) as connection:
            return connection.execute(
                """
                SELECT id, user_id, resource_id, booking_date, start_time,
                       end_time, status
                FROM bookings
                ORDER BY id
                """
            ).fetchall()

    def test_authenticated_user_can_view_facilities_and_own_bookings(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Lecture Hall 1", response.data)
        self.assertIn(b"Find a space", response.data)

        response = self.submit_booking()
        self.assertIn(b"Booking request submitted. Status: Pending.", response.data)
        self.assertIn(b"My bookings", response.data)
        self.assertIn(b"Project meeting", response.data)
        self.assertEqual(len(self.stored_bookings()), 1)
        self.assertEqual(self.stored_bookings()[0]["status"], "pending")

    def test_availability_search_marks_conflicting_facility_as_booked(self):
        self.submit_booking(start_time="09:00", end_time="10:00")
        response = self.client.get(
            f"/dashboard?date={self.booking_date}&start_time=09:30&end_time=10:30"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Booked", response.data)

    def test_overlapping_booking_is_rejected(self):
        self.submit_booking(start_time="09:00", end_time="10:00")
        response = self.submit_booking(start_time="09:30", end_time="10:30")
        self.assertIn(b"already booked", response.data)
        self.assertEqual(len(self.stored_bookings()), 1)

    def test_adjacent_time_slots_are_allowed(self):
        self.submit_booking(start_time="09:00", end_time="10:00")
        response = self.submit_booking(start_time="10:00", end_time="11:00")
        self.assertIn(b"Booking request submitted.", response.data)
        self.assertEqual(len(self.stored_bookings()), 2)

    def test_different_facility_and_different_date_do_not_conflict(self):
        self.submit_booking(resource_id=1, start_time="09:00", end_time="10:00")
        response = self.submit_booking(resource_id=2, start_time="09:30", end_time="10:30")
        self.assertIn(b"Booking request submitted.", response.data)
        response = self.submit_booking(
            resource_id=1,
            booking_date="2099-01-16",
            start_time="09:30",
            end_time="10:30",
        )
        self.assertIn(b"Booking request submitted.", response.data)
        self.assertEqual(len(self.stored_bookings()), 3)

    def test_invalid_dates_times_resource_and_purpose_are_rejected(self):
        cases: list[dict[str, str]] = [
            {"booking_date": "not-a-date"},
            {"booking_date": "2000-01-01"},
            {"start_time": "10:00", "end_time": "10:00"},
            {"start_time": "11:00", "end_time": "10:00"},
            {"start_time": "9:00"},
            {"resource_id": "99999"},
            {"purpose": "   "},
            {"purpose": "x" * 501},
        ]
        for values in cases:
            with self.subTest(values=values):
                form_data = {
                    "csrf_token": self.get_csrf_token(),
                    "resource_id": "1",
                    "booking_date": self.booking_date,
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "purpose": "Project meeting",
                }
                form_data.update(values)
                self.client.post("/bookings", data=form_data, follow_redirects=True)
        self.assertEqual(len(self.stored_bookings()), 0)

    def test_same_day_booking_starting_in_the_past_is_rejected(self):
        now = datetime.now().astimezone()
        past_minute = now - timedelta(minutes=1)
        if past_minute.date() != now.date():
            self.skipTest("Current local time is too close to midnight for this case.")

        response = self.submit_booking(
            booking_date=now.date().isoformat(),
            start_time=past_minute.strftime("%H:%M"),
            end_time=(past_minute + timedelta(minutes=1)).strftime("%H:%M"),
        )
        self.assertIn(b"Choose a valid date and time range.", response.data)
        self.assertEqual(len(self.stored_bookings()), 0)

    def test_inactive_facility_cannot_be_booked(self):
        with get_connection(self.database_path) as connection:
            connection.execute("UPDATE resources SET is_active = 0 WHERE id = 1")

        response = self.submit_booking(resource_id=1)
        self.assertIn(b"That facility is unavailable.", response.data)
        self.assertEqual(len(self.stored_bookings()), 0)

    def test_cancelled_booking_no_longer_blocks_the_time_slot(self):
        self.submit_booking()
        booking_id = self.stored_bookings()[0]["id"]
        response = self.client.post(
            f"/bookings/{booking_id}/cancel",
            data={"csrf_token": self.get_csrf_token()},
            follow_redirects=True,
        )
        self.assertIn(b"Booking cancelled.", response.data)
        self.assertEqual(self.stored_bookings()[0]["status"], "cancelled")

        response = self.submit_booking()
        self.assertIn(b"Booking request submitted.", response.data)
        self.assertEqual(len(self.stored_bookings()), 2)

    def test_rejected_booking_does_not_block_the_time_slot(self):
        self.submit_booking()
        booking_id = self.stored_bookings()[0]["id"]
        with get_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE bookings SET status = 'rejected' WHERE id = ?",
                (booking_id,),
            )

        response = self.submit_booking()
        self.assertIn(b"Booking request submitted.", response.data)
        self.assertEqual(len(self.stored_bookings()), 2)

    def test_user_cannot_cancel_another_users_booking(self):
        self.submit_booking(purpose="Private student booking")
        booking_id = self.stored_bookings()[0]["id"]
        self.client = self.app.test_client()
        self.login_as("staff@campus.edu", "Staff123!")

        response = self.client.post(
            f"/bookings/{booking_id}/cancel",
            data={"csrf_token": self.get_csrf_token()},
            follow_redirects=True,
        )
        self.assertIn(b"That booking cannot be cancelled.", response.data)
        self.assertEqual(self.stored_bookings()[0]["status"], "pending")

    def test_user_cannot_view_another_users_bookings(self):
        self.submit_booking(purpose="Private student booking")
        self.client = self.app.test_client()
        self.login_as("staff@campus.edu", "Staff123!")

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Private student booking", response.data)

    def test_booking_routes_require_login_and_csrf(self):
        self.client = self.app.test_client()
        self.assertEqual(self.client.get("/dashboard").status_code, 302)
        self.assertEqual(
            self.client.post("/bookings", data={}).status_code,
            302,
        )

        self.login_as("student@campus.edu", "Student123!")
        response = self.client.post(
            "/bookings",
            data={
                "resource_id": "1",
                "booking_date": self.booking_date,
                "start_time": "09:00",
                "end_time": "10:00",
                "purpose": "No CSRF token",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.stored_bookings()), 0)


if __name__ == "__main__":
    unittest.main()
