import uuid
import unittest

from app import app
from database import get_db


class AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.db = get_db()
        self.uid = uuid.uuid4().hex[:8]

        self.software_name = f"TestSoftware-{self.uid}"
        self.hostname = f"test-pc-{self.uid}"

        self.db.execute(
            """
            INSERT INTO software (name, version, type, install_command, category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.software_name, "1.0", "online", "choco install 7zip -y", "Utility"),
        )
        software_row = self.db.execute(
            "SELECT id FROM software WHERE name = ?",
            (self.software_name,),
        ).fetchone()
        self.software_id = software_row["id"]

        self.db.execute(
            """
            INSERT INTO computers (hostname, ip_address, status)
            VALUES (?, ?, ?)
            """,
            (self.hostname, "127.0.0.1", "online"),
        )

        self.db.execute(
            """
            INSERT INTO jobs (computer_hostname, software_id, status)
            VALUES (?, ?, 'pending')
            """,
            (self.hostname, self.software_id),
        )
        self.db.commit()

    def tearDown(self):
        self.db.execute(
            "DELETE FROM jobs WHERE software_id = ?",
            (self.software_id,),
        )
        self.db.execute(
            "DELETE FROM computers WHERE hostname = ?",
            (self.hostname,),
        )
        self.db.execute(
            "DELETE FROM software WHERE id = ?",
            (self.software_id,),
        )
        self.db.commit()

    def test_poll_returns_job_and_marks_in_progress(self):
        response = self.client.post(
            "/api/agent/poll",
            json={"computer_hostname": self.hostname, "ip_address": "127.0.0.10"},
        )
        self.assertEqual(response.status_code, 200)

        payload = response.get_json()
        self.assertIsNotNone(payload["job"])
        self.assertEqual(payload["job"]["software_id"], self.software_id)

        db_job = self.db.execute(
            "SELECT status FROM jobs WHERE id = ?",
            (payload["job"]["id"],),
        ).fetchone()
        self.assertEqual(db_job["status"], "in_progress")

    def test_poll_no_job_returns_null(self):
        self.db.execute(
            "DELETE FROM jobs WHERE computer_hostname = ? AND software_id = ?",
            (self.hostname, self.software_id),
        )
        self.db.commit()

        response = self.client.post(
            "/api/agent/poll",
            json={"computer_hostname": self.hostname},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"job": None})

    def test_report_updates_job_to_completed(self):
        poll_response = self.client.post(
            "/api/agent/poll",
            json={"computer_hostname": self.hostname},
        )
        job_id = poll_response.get_json()["job"]["id"]

        report_response = self.client.post(
            f"/api/agent/jobs/{job_id}/report",
            json={"computer_hostname": self.hostname, "status": "completed"},
        )
        self.assertEqual(report_response.status_code, 200)

        db_job = self.db.execute(
            "SELECT status, completed_at FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        self.assertEqual(db_job["status"], "completed")
        self.assertIsNotNone(db_job["completed_at"])

    def test_report_rejects_wrong_hostname(self):
        poll_response = self.client.post(
            "/api/agent/poll",
            json={"computer_hostname": self.hostname},
        )
        job_id = poll_response.get_json()["job"]["id"]

        report_response = self.client.post(
            f"/api/agent/jobs/{job_id}/report",
            json={"computer_hostname": f"wrong-{self.hostname}", "status": "failed"},
        )
        self.assertEqual(report_response.status_code, 403)

    def test_report_rejects_if_not_in_progress(self):
        job_row = self.db.execute(
            "SELECT id FROM jobs WHERE computer_hostname = ? ORDER BY id DESC LIMIT 1",
            (self.hostname,),
        ).fetchone()

        report_response = self.client.post(
            f"/api/agent/jobs/{job_row['id']}/report",
            json={"computer_hostname": self.hostname, "status": "completed"},
        )
        self.assertEqual(report_response.status_code, 409)


if __name__ == "__main__":
    unittest.main()

