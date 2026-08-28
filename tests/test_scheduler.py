"""Local IST corpus scheduler. Does not scrape Groww in these tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from api.main import app
from src.scheduler import (
    IST,
    SchedulerError,
    create_schedule,
    default_execute,
    dispatch_github_ingest,
    fire_due_schedules,
    github_ref,
    load_runs,
    next_run_at,
    parse_times,
    scheduler_backend,
    should_run_loop,
    update_schedule,
)


class SchedulerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        os.environ["SCHEDULER_DIR"] = str(self.folder)
        os.environ["SCHEDULER_INLINE"] = "1"
        self.addCleanup(lambda: os.environ.pop("SCHEDULER_DIR", None))
        self.addCleanup(lambda: os.environ.pop("SCHEDULER_INLINE", None))

    def test_parse_times_normalizes_and_rejects_junk(self) -> None:
        self.assertEqual(parse_times(["23:00", "10:03", "10:03:00"]), ["10:03", "23:00"])
        with self.assertRaises(ValueError):
            parse_times(["25:00"])

    def test_paused_schedule_does_not_fire(self) -> None:
        row = create_schedule("twice", ["10:03"])
        update_schedule(row["id"], enabled=False)
        fired = fire_due_schedules(
            datetime(2026, 8, 27, 10, 3, tzinfo=IST),
            execute=lambda: (True, "ok"),
        )
        self.assertEqual(fired, [])
        self.assertEqual(load_runs(), [])

    def test_due_time_runs_once_per_slot(self) -> None:
        row = create_schedule("morning", ["10:03"])
        now = datetime(2026, 8, 27, 10, 3, tzinfo=IST)
        first = fire_due_schedules(now, execute=lambda: (True, "refreshed"))
        second = fire_due_schedules(now, execute=lambda: (True, "again"))
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        runs = load_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertEqual(runs[0]["result"], "refreshed")
        self.assertEqual(runs[0]["schedule_id"], row["id"])

    def test_next_run_rolls_to_tomorrow(self) -> None:
        now = datetime(2026, 8, 27, 23, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        nxt = next_run_at(["10:03", "23:00"], now)
        self.assertIsNotNone(nxt)
        self.assertIn("2026-08-28", nxt or "")
        self.assertIn("10:03", nxt or "")

    def test_loop_is_skipped_in_tests(self) -> None:
        self.assertFalse(should_run_loop())

    def test_default_backend_is_github_on_main(self) -> None:
        os.environ.pop("SCHEDULER_BACKEND", None)
        self.assertEqual(scheduler_backend(), "github")
        self.assertEqual(github_ref(), "main")

    def test_github_dispatch_requires_token(self) -> None:
        os.environ["SCHEDULER_BACKEND"] = "github"
        os.environ.pop("SCHEDULER_GITHUB_TOKEN", None)
        os.environ.pop("GITHUB_TOKEN", None)
        with self.assertRaises(SchedulerError) as ctx:
            default_execute()
        self.assertIn("remote main", str(ctx.exception))

    def test_github_dispatch_posts_ref_main(self) -> None:
        os.environ["SCHEDULER_GITHUB_TOKEN"] = "ghs_test_token"
        os.environ["SCHEDULER_GITHUB_REF"] = "main"
        calls: list[tuple] = []

        def fake_request(method, url, token, payload=None):
            calls.append((method, url, payload))
            if method == "POST":
                return {}
            return {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "html_url": "https://github.com/manisdevs-git/mfchatbot/actions/runs/1",
                        "head_sha": "abc1234deadbeef",
                        "head_branch": "main",
                        "created_at": "2099-01-01T00:00:00Z",
                    }
                ]
            }

        with patch("src.scheduler._github_request", side_effect=fake_request):
            ok, text = dispatch_github_ingest()
        self.assertTrue(ok)
        self.assertEqual(calls[0][0], "POST")
        self.assertEqual(calls[0][2]["ref"], "main")
        self.assertIn("refresh-corpus.yml/dispatches", calls[0][1])
        self.assertIn("remote branch files", text)
        self.assertNotIn("ghs_test_token", text)


class SchedulerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["SCHEDULER_DIR"] = self.tmp.name
        os.environ["SCHEDULER_INLINE"] = "1"
        self.addCleanup(lambda: os.environ.pop("SCHEDULER_DIR", None))
        self.addCleanup(lambda: os.environ.pop("SCHEDULER_INLINE", None))
        self.client = TestClient(app)

    def test_create_pause_run_delete_and_history(self) -> None:
        created = self.client.post(
            "/v1/schedules",
            json={"name": "Night scrape", "times": ["23:00"]},
        )
        self.assertEqual(created.status_code, 200)
        schedule_id = created.json()["schedule"]["id"]
        self.assertTrue(created.json()["schedule"]["enabled"])
        self.assertEqual(created.json()["schedule"]["times"], ["23:00"])

        paused = self.client.patch(f"/v1/schedules/{schedule_id}", json={"enabled": False})
        self.assertTrue(paused.json()["schedule"]["paused"])

        with patch("api.main.start_run") as mocked:
            mocked.return_value = {
                "id": "run-1",
                "schedule_id": schedule_id,
                "schedule_name": "Night scrape",
                "trigger": "manual",
                "status": "running",
                "started_at": "2026-08-27T23:00:00+05:30",
                "finished_at": None,
                "duration_ms": None,
                "result": "Corpus refresh started.",
            }
            ran = self.client.post(f"/v1/schedules/{schedule_id}/run")
        self.assertEqual(ran.status_code, 200)
        mocked.assert_called_once()

        listed = self.client.get("/v1/schedules")
        self.assertEqual(len(listed.json()["schedules"]), 1)

        deleted = self.client.delete(f"/v1/schedules/{schedule_id}")
        self.assertEqual(deleted.status_code, 200)
        empty = self.client.get("/v1/schedules")
        self.assertEqual(empty.json()["schedules"], [])

    def test_bad_time_is_400(self) -> None:
        response = self.client.post("/v1/schedules", json={"name": "bad", "times": ["99:99"]})
        self.assertEqual(response.status_code, 400)

    def test_root_lists_scheduler(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.json()["scheduler"], "GET /v1/schedules")


if __name__ == "__main__":
    unittest.main()
