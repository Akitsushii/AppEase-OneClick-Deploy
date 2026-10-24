import uuid
import unittest

from app import app
from database import get_db


class ProblemReportApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.db = get_db()
        self.uid = uuid.uuid4().hex[:8]
        self.hostname = f"report-pc-{self.uid}"

    def tearDown(self):
        self.db.execute(
            "DELETE FROM problem_reports WHERE computer_hostname = ?",
            (self.hostname,),
        )
        self.db.execute(
            "DELETE FROM computers WHERE hostname = ?",
            (self.hostname,),
        )
        self.db.commit()

    def test_submit_problem_report_success(self):
        response = self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "hardware",
                "description": "Keyboard not responding",
            },
        )
        self.assertEqual(response.status_code, 201)

        report = self.db.execute(
            "SELECT problem_type, description, status FROM problem_reports WHERE computer_hostname = ?",
            (self.hostname,),
        ).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(report["problem_type"], "hardware")
        self.assertEqual(report["status"], "open")

    def test_submit_problem_report_validation_errors(self):
        missing_hostname = self.client.post(
            "/api/agent/problem-reports",
            json={
                "problem_type": "hardware",
                "description": "Monitor flickering",
            },
        )
        self.assertEqual(missing_hostname.status_code, 400)

        bad_type = self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "power",
                "description": "PC does not boot",
            },
        )
        self.assertEqual(bad_type.status_code, 400)

        missing_description = self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "hardware",
                "description": "",
            },
        )
        self.assertEqual(missing_description.status_code, 400)

    def test_dashboard_shows_problem_report(self):
        self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "network",
                "description": "No internet access",
            },
        )

        dashboard_response = self.client.get("/")
        self.assertEqual(dashboard_response.status_code, 200)
        page = dashboard_response.data.decode("utf-8")
        self.assertIn("Student Problem Reports", page)
        self.assertIn(self.hostname, page)
        self.assertIn("No internet access", page)

    def test_admin_can_update_problem_report_status(self):
        create_response = self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "software",
                "description": "Application keeps crashing",
            },
        )
        report_id = create_response.get_json()["report_id"]

        update_response = self.client.post(
            f"/problem-reports/{report_id}/status",
            data={"status": "resolved"},
            follow_redirects=True,
        )
        self.assertEqual(update_response.status_code, 200)

        report = self.db.execute(
            "SELECT status FROM problem_reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "resolved")

    def test_admin_update_rejects_invalid_status(self):
        create_response = self.client.post(
            "/api/agent/problem-reports",
            json={
                "computer_hostname": self.hostname,
                "problem_type": "hardware",
                "description": "Webcam is not detected",
            },
        )
        report_id = create_response.get_json()["report_id"]

        update_response = self.client.post(
            f"/problem-reports/{report_id}/status",
            data={"status": "closed"},
            follow_redirects=True,
        )
        self.assertEqual(update_response.status_code, 200)

        report = self.db.execute(
            "SELECT status FROM problem_reports WHERE id = ?",
            (report_id,),
        ).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "open")
        self.assertIn("Invalid problem report status selected.", update_response.data.decode("utf-8"))

    def test_admin_update_handles_missing_report(self):
        update_response = self.client.post(
            "/problem-reports/999999/status",
            data={"status": "resolved"},
            follow_redirects=True,
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertIn("Problem report not found.", update_response.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

