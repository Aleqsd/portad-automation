import io
import json
import os
import tempfile
import unittest
import sys
from unittest.mock import MagicMock, patch

import gzip
from pathlib import Path

from bs4 import BeautifulSoup

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
        with patch("fetch_portad_dashboard.build_session") as session_factory:
            fpd.send_pushover("hello")
        session_factory.assert_not_called()

    def test_send_pushover_posts_with_tokens(self):
        os.environ["PUSHOVER_API_TOKEN"] = "token-123"
        os.environ["PUSHOVER_USER_KEY"] = "user-456"
        resp = MagicMock()
        resp.status_code = 200
        session = MagicMock()
        session.post.return_value = resp
        session.__enter__.return_value = session
        with patch("fetch_portad_dashboard.build_session", return_value=session):
            fpd.send_pushover("hello", title="Test title")
        session.post.assert_called_once()
        kwargs = session.post.call_args.kwargs
        self.assertEqual(session.post.call_args.args[0], "https://api.pushover.net/1/messages.json")
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
        session = MagicMock()
        session.post.side_effect = Exception("boom")
        session.__enter__.return_value = session
        with patch("fetch_portad_dashboard.build_session", return_value=session), patch("sys.stderr", err):
            # Should not raise
            fpd.send_pushover("hello")

    def test_send_pushover_logs_on_non_200(self):
        os.environ["PUSHOVER_API_TOKEN"] = "token-123"
        os.environ["PUSHOVER_USER_KEY"] = "user-456"

        resp = MagicMock()
        resp.status_code = 400
        resp.text = "bad request"
        session = MagicMock()
        session.post.return_value = resp
        session.__enter__.return_value = session
        err = io.StringIO()
        with patch("fetch_portad_dashboard.build_session", return_value=session), patch("sys.stderr", err):
            fpd.send_pushover("hello")
        self.assertIn("Pushover failed (400)", err.getvalue())


class ParseHtmlTests(unittest.TestCase):
    def test_parse_tile_counters_extracts_details_and_percent(self):
        html = """
        <div>
          <div class="tile-counter">
            <h5>Disponible</h5>
            <h2>10 €</h2>
            <div class="row"><span>Facturé</span><span>5 €</span></div>
            <span data-percent="50"></span>
          </div>
          <div class="tile-counter"><h5>Autre</h5><h2>3</h2></div>
          <div class="tile-counter"><h5>Ignored</h5></div>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        tiles = fpd.parse_tile_counters(soup)
        self.assertEqual(len(tiles), 2)
        self.assertEqual(tiles[0]["label"], "Disponible")
        self.assertEqual(tiles[0]["value"], "10 €")
        self.assertIn("Facturé 5 €", tiles[0]["details"])
        self.assertEqual(tiles[0]["percent"], "50")
        self.assertEqual(tiles[1]["label"], "Autre")
        self.assertEqual(tiles[1]["value"], "3")

    def test_parse_two_col_tables_maps_headers_and_headings(self):
        html = """
        <ul><li><a href="#tab-a">Synthèse</a></li></ul>
        <div class="tab-pane" id="tab-a">
          <table>
            <thead><tr><th>Col A</th><th>Col B</th></tr></thead>
            <tbody><tr><td>a1</td><td>b1</td></tr></tbody>
          </table>
        </div>
        <h3>Résumé</h3>
        <table>
          <tr><td>Total</td><td>100</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "lxml")
        tables = fpd.parse_two_col_tables(soup)
        self.assertEqual(len(tables), 2)
        first, second = tables
        self.assertEqual(first["heading"], "Synthèse")
        self.assertIn({"Col A": "a1", "Col B": "b1"}, first["rows"])
        self.assertEqual(second["heading"], "Résumé")
        self.assertEqual(second["rows"][0], ("Total", "100"))


class SnapshotTests(unittest.TestCase):
    def test_atomic_dump_and_save_snapshot_write_json_and_gzip(self):
        data = {"hello": "world"}
        with tempfile.TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "snaps"
            last = snap_dir / "last_snapshot.json"
            with patch("fetch_portad_dashboard.SNAPSHOT_DIR", snap_dir), patch(
                "fetch_portad_dashboard.LAST_SNAPSHOT", last
            ):
                snap_path = fpd.save_snapshot(data)
                self.assertTrue(snap_path.exists())
                with gzip.open(snap_path, "rt", encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh), data)
                with last.open("r", encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh), data)

    def test_cleanup_old_snapshots_respects_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap_dir = Path(tmp) / "snaps"
            snap_dir.mkdir()
            # Create 5 fake gz files; keep last 3
            for idx in range(5):
                (snap_dir / f"portad-dashboard-20240101-0{idx}.json.gz").write_text(
                    "x", encoding="utf-8"
                )
            keep = 3
            with patch("fetch_portad_dashboard.SNAPSHOT_DIR", snap_dir), patch(
                "fetch_portad_dashboard.SNAPSHOT_RETENTION", keep
            ):
                fpd.cleanup_old_snapshots()
            remaining = sorted(snap_dir.glob("*.gz"))
            self.assertEqual(len(remaining), keep)
            names = [p.name for p in remaining]
            self.assertEqual(
                names,
                [
                    "portad-dashboard-20240101-02.json.gz",
                    "portad-dashboard-20240101-03.json.gz",
                    "portad-dashboard-20240101-04.json.gz",
                ],
            )


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

    def test_summarize_changes_reports_added_tile(self):
        prev = {"tiles": [], "tables": []}
        curr = {"tiles": [{"label": "Disponible", "value": "5 €"}], "tables": []}
        summary = fpd.summarize_changes(prev, curr)
        self.assertIn("Disponible", summary)
        self.assertIn("- -> 5 €", summary)

    def test_summarize_changes_uses_fallback_diff(self):
        prev = {"tiles": [{"label": "Disponible", "value": "1 €", "details": ["old"]}], "tables": []}
        curr = {"tiles": [{"label": "Disponible", "value": "1 €", "details": ["new"]}], "tables": []}
        summary = fpd.summarize_changes(prev, curr)
        self.assertIn("details", summary)
        self.assertIn("old", summary)
        self.assertIn("new", summary)
        self.assertIn("->", summary)

    def test_summarize_changes_handles_duplicates_and_reorder(self):
        prev = {
            "tiles": [
                {"label": "Disponible", "value": "1"},
                {"label": "Disponible", "value": "2"},
            ],
            "tables": [
                {"heading": "A", "rows": [1]},
                {"heading": "B", "rows": [1, 2]},
            ],
        }
        curr = {
            "tiles": [
                {"label": "Disponible", "value": "1"},
                {"label": "Disponible", "value": "5"},
            ],
            "tables": [
                {"heading": "B", "rows": [1, 2]},
                {"heading": "A", "rows": [1, 2, 3]},
            ],
        }
        summary = fpd.summarize_changes(prev, curr)
        self.assertIn("Disponible #2", summary)
        self.assertIn("2 -> 5", summary)
        self.assertIn("Tableaux", summary)
        self.assertIn("1 lignes -> 3 lignes", summary)


class NotificationMessageTests(unittest.TestCase):
    def test_build_notification_message_uses_fallback_when_summary_blank(self):
        msg = fpd.build_notification_message("   \n", Path("portad-dashboard-123.json.gz"))
        self.assertIn("Changement détecté", msg)
        self.assertIn("portad-dashboard-123.json.gz", msg)

    def test_build_notification_message_trims_and_preserves_lines(self):
        summary = "  A -> B  \n\nC -> D"
        msg = fpd.build_notification_message(summary, None)
        self.assertEqual(msg, "A -> B\nC -> D")


class SessionAndAuthTests(unittest.TestCase):
    def test_build_session_sets_user_agent_and_retries(self):
        session = fpd.build_session()
        self.assertEqual(session.headers.get("User-Agent"), fpd.USER_AGENT)
        adapter = session.get_adapter("https://")
        self.assertEqual(adapter.max_retries.total, 3)

    def test_is_login_page(self):
        login_html = '<form><input name="login[username]"><input name="login[password]"></form>'
        self.assertTrue(fpd._is_login_page(login_html))
        self.assertFalse(fpd._is_login_page("<html>ok</html>"))

    def test_main_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True), patch("fetch_portad_dashboard.load_env_file"):
            with patch.object(sys, "argv", ["prog"]):
                with self.assertRaises(SystemExit):
                    fpd.main()


if __name__ == "__main__":
    unittest.main()
