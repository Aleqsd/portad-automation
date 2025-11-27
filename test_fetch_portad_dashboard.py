import io
import os
import unittest
from unittest.mock import patch

import fetch_portad_dashboard as fpd


class SendPushoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_send_pushover_skips_without_tokens(self):
        os.environ.pop("PUSHOVER_API_TOKEN", None)
        os.environ.pop("PUSHOVER_USER_KEY", None)
        with patch("fetch_portad_dashboard.requests.post") as post_mock:
            fpd.send_pushover("hello")
        post_mock.assert_not_called()

    def test_send_pushover_posts_with_tokens(self):
        os.environ["PUSHOVER_API_TOKEN"] = "token-123"
        os.environ["PUSHOVER_USER_KEY"] = "user-456"
        with patch("fetch_portad_dashboard.requests.post") as post_mock:
            post_mock.return_value.status_code = 200
            fpd.send_pushover("hello", title="Test title")
        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://api.pushover.net/1/messages.json")
        payload = kwargs.get("data", {})
        self.assertEqual(payload["token"], "token-123")
        self.assertEqual(payload["user"], "user-456")
        self.assertEqual(payload["title"], "Test title")
        self.assertEqual(payload["message"], "hello")
        self.assertEqual(payload["priority"], 0)
        self.assertEqual(kwargs.get("timeout"), 10)

    def test_send_pushover_swallows_post_errors(self):
        os.environ["PUSHOVER_API_TOKEN"] = "token-123"
        os.environ["PUSHOVER_USER_KEY"] = "user-456"
        err = io.StringIO()
        with patch("fetch_portad_dashboard.requests.post", side_effect=Exception("boom")), patch(
            "sys.stderr", err
        ):
            # Should not raise
            fpd.send_pushover("hello")

    def test_send_pushover_logs_on_non_200(self):
        os.environ["PUSHOVER_API_TOKEN"] = "token-123"
        os.environ["PUSHOVER_USER_KEY"] = "user-456"

        class Resp:
            def __init__(self):
                self.status_code = 400
                self.text = "bad request"

        err = io.StringIO()
        with patch("fetch_portad_dashboard.requests.post", return_value=Resp()), patch(
            "sys.stderr", err
        ):
            fpd.send_pushover("hello")
        self.assertIn("Pushover failed (400)", err.getvalue())


class SummarizeChangesTests(unittest.TestCase):
    def test_summarize_changes_includes_tiles_and_rows(self):
        prev = {
            "tiles": [{"label": "Disponible", "value": "1 €"}],
            "tables": [{"heading": "Synthèse", "rows": [1, 2, 3]}],
        }
        curr = {
            "tiles": [{"label": "Disponible", "value": "2 €"}],
            "tables": [{"heading": "Synthèse", "rows": [1, 2, 3, 4]}],
        }
        summary = fpd.summarize_changes(prev, curr)
        self.assertIn("Disponible", summary)
        self.assertIn("1 € -> 2 €", summary)
        self.assertIn("Synthèse : 3 lignes -> 4 lignes", summary)

    def test_summarize_changes_first_snapshot(self):
        self.assertEqual(
            fpd.summarize_changes(None, {"tiles": [], "tables": []}),
            "Première capture enregistrée.",
        )


if __name__ == "__main__":
    unittest.main()
