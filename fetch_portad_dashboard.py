#!/usr/bin/env python3
"""
Fetch and parse the LAYA dashboard (display-tableau) once using requests + BeautifulSoup.

Usage:
    PORTAD_USER=you@example.com PORTAD_PASS=secret .venv/bin/python fetch_portad_dashboard.py

Environment variables (or a .env file) must define credentials.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://portad.laya.fr/"
LOGIN_URL = BASE_URL + "?ext=loginpage&controller=ext&action=login"
DISPLAY_TABLEAU_PAGE = BASE_URL + "index.php?new=1&id=display-tableau"
AJAX_PERSON_URL = BASE_URL + "index.php?ext=contact&controller=person"

# Snapshot storage
SNAPSHOT_DIR = Path("snapshots")
LAST_SNAPSHOT = SNAPSHOT_DIR / "last_snapshot.json"
SNAPSHOT_RETENTION = 30  # keep last 30 gzip snapshots
HTTP_TIMEOUT = 20
USER_AGENT = "portad-automation/1.0 (+https://github.com/)"


def load_env_file(path: str = ".env") -> None:
    """Lightweight .env loader to avoid extra dependencies."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def build_session() -> requests.Session:
    """Create a session with retry/backoff and a consistent User-Agent."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def login(session: requests.Session, username: str, password: str) -> str:
    """Perform a single login and return the HTML of the landing page."""
    # Prime session with initial GET to set cookies
    session.get(BASE_URL, timeout=HTTP_TIMEOUT)

    payload = {
        "login[username]": username,
        "login[password]": password,
        "login[submit]": "Se connecter",
        "rememberme": "forever",
        "login[redirectURL]": "",
        "login[ope]": "",
        "login[isMobileApp]": "",
    }

    resp = session.post(
        LOGIN_URL,
        data=payload,
        allow_redirects=True,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def extract_user_id(html: str) -> str | None:
    """Grab the logged-in user id from a page (hidden input id_person_conn)."""
    soup = BeautifulSoup(html, "lxml")
    user_id_input = soup.find("input", id="id_person_conn")
    if user_id_input and user_id_input.get("value"):
        return user_id_input["value"]
    return None


def fetch_dashboard_html(session: requests.Session, user_id: str) -> str:
    """Call the same AJAX endpoint the UI uses to render the tableau."""
    payload = {
        "person": user_id,
        "filtrer": 0,
        "page": 1,
        "encours": 3,
        "sSearch": "",
        "ids": "()",
        "idRoles": "()",
        "statuts": "()",
        "type": "tableau",
        "id": "",
        "renouv": -1,
        "action": "display",
    }
    headers = {"X-Requested-With": "XMLHttpRequest"}
    resp = session.post(
        AJAX_PERSON_URL, data=payload, headers=headers, timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()

    if resp.headers.get("Todoyu-Msginterdit") == "1":
        raise RuntimeError("Server denied access to tableau (msginterdit=1)")

    return resp.text


def parse_tile_counters(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract the top row KPI tiles (label, value, optional details/percent)."""
    tiles: List[Dict[str, str]] = []
    for tile in soup.select(".tile-counter"):
        value_tag = tile.find("h2")
        label_tag = tile.find(["h5", "h4"])

        # Skip navigation tiles that don't carry numeric content
        if not value_tag and not tile.select_one("[data-percent]"):
            continue

        entry: Dict[str, str] = {
            "label": label_tag.get_text(strip=True) if label_tag else "",
            "value": value_tag.get_text(strip=True) if value_tag else "",
        }

        # Optional sub-lines (e.g., Facturé / Non facturé breakdown)
        details = []
        for row in tile.select(".row"):
            children = row.find_all(recursive=False)
            if not children:
                continue
            row_text = " ".join(c.get_text(" ", strip=True) for c in children)
            if row_text:
                details.append(row_text)
        if details:
            entry["details"] = details  # type: ignore[assignment]

        percent_span = tile.select_one("[data-percent]")
        if percent_span and percent_span.get("data-percent"):
            entry["percent"] = percent_span["data-percent"]

        tiles.append(entry)
    return tiles


def _resolve_heading(table, soup: BeautifulSoup) -> str | None:
    """Find a human-friendly heading for a table."""
    # 1) If table sits inside a tab-pane, try nav label
    pane = table.find_parent(class_="tab-pane")
    if pane and pane.get("id"):
        link = soup.select_one(f"a[href='#{pane['id']}']")
        if link and link.get_text(strip=True):
            return link.get_text(strip=True)

    # 2) Nearest previous heading outside modals
    for elem in table.find_all_previous():
        if elem.name in ("h2", "h3", "h4", "h5"):
            if elem.get_text(strip=True).lower() == "responsive modal":
                continue
            if elem.find_parent(class_="modal"):
                continue
            text = elem.get_text(strip=True)
            if text:
                return text
    return None


def parse_two_col_tables(soup: BeautifulSoup) -> List[Dict[str, object]]:
    """Parse every table; keep two-col as tuples, but also keep full headers map."""
    tables: List[Dict[str, object]] = []
    for table in soup.find_all("table"):
        heading = _resolve_heading(table, soup)

        # Headers
        header_cells = table.find("thead").find_all("th") if table.find("thead") else []
        headers = [
            (th.get_text(" ", strip=True) or f"col{idx}")
            for idx, th in enumerate(header_cells)
        ]

        rows: List[object] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            if headers and len(cells) == len(headers):
                row_map = {
                    headers[i]: cells[i].get_text(" ", strip=True)
                    for i in range(len(headers))
                }
                rows.append(row_map)
            elif len(cells) >= 2:
                key = cells[0].get_text(" ", strip=True)
                val = cells[1].get_text(" ", strip=True)
                rows.append((key, val))

        if rows:
            tables.append({"heading": heading, "headers": headers, "rows": rows})
    return tables


def save_snapshot(data: dict) -> Path:
    """
    Save current data to:
      - gzip JSON snapshot with timestamp (space efficient)
      - plain JSON last_snapshot.json (diff-friendly)
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_path = SNAPSHOT_DIR / f"portad-dashboard-{ts}.json.gz"
    _atomic_dump_json(snap_path, data, gzip_compress=True)
    _atomic_dump_json(LAST_SNAPSHOT, data, gzip_compress=False)
    return snap_path


def cleanup_old_snapshots():
    gz_files = sorted(SNAPSHOT_DIR.glob("portad-dashboard-*.json.gz"), reverse=True)
    if len(gz_files) <= SNAPSHOT_RETENTION:
        return
    for old in gz_files[SNAPSHOT_RETENTION:]:
        try:
            old.unlink()
        except Exception:
            pass


def load_last_snapshot() -> dict | None:
    if not LAST_SNAPSHOT.exists():
        return None
    try:
        with LAST_SNAPSHOT.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def diff_changed(prev: dict, curr: dict) -> bool:
    """Simple structural diff: True if serialized views differ."""
    return json.dumps(prev, sort_keys=True) != json.dumps(curr, sort_keys=True)


def send_pushover(message: str, title: str = "Portad dashboard update") -> None:
    token = os.getenv("PUSHOVER_API_TOKEN")
    user = os.getenv("PUSHOVER_USER_KEY")
    if not token or not user:
        return  # silent if not configured
    payload = {
        "token": token,
        "user": user,
        "title": title,
        "message": message,
        "priority": 0,
    }
    try:
        with build_session() as session:
            resp = session.post(
                "https://api.pushover.net/1/messages.json",
                data=payload,
                timeout=10,
            )
            if resp.status_code != 200:
                sys.stderr.write(
                    f"Pushover failed ({resp.status_code}): {resp.text[:200]}\n"
                )
    except Exception as exc:
        # non-fatal, but keep a trace
        sys.stderr.write(f"Pushover error: {exc}\n")


def notify_error(exc: Exception) -> None:
    msg = f"Echec Portad: {exc}"
    send_pushover(msg, title="Portad dashboard ERROR")
    sys.stderr.write(msg + "\n")


def _stringify_value(val: Any, max_len: int = 120) -> str:
    text = json.dumps(val, ensure_ascii=False)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (list, dict)):
        return _stringify_value(value)
    return str(value)


def _build_label_map(
    entries: List[dict], label_key: str, fallback_prefix: str
) -> Dict[str, dict]:
    seen_counts: Dict[str, int] = {}
    mapping: Dict[str, dict] = {}
    for idx, entry in enumerate(entries):
        raw_label = entry.get(label_key)
        label = raw_label.strip() if isinstance(raw_label, str) else ""
        if not label:
            label = f"{fallback_prefix} {idx + 1}"
        count = seen_counts.get(label, 0)
        seen_counts[label] = count + 1
        unique_label = label if count == 0 else f"{label} #{count + 1}"
        mapping[unique_label] = entry
    return mapping


def _summarize_tile_changes(prev_tiles: List[dict], curr_tiles: List[dict]) -> List[str]:
    lines: List[str] = []
    prev_map = _build_label_map(prev_tiles, "label", "Tile")
    curr_map = _build_label_map(curr_tiles, "label", "Tile")
    keys = list(curr_map.keys()) + [k for k in prev_map if k not in curr_map]

    for key in keys:
        prev_tile = prev_map.get(key)
        curr_tile = curr_map.get(key)
        if prev_tile and curr_tile:
            if prev_tile.get("value") != curr_tile.get("value"):
                lines.append(
                    f"📊 {key} : {_format_value(prev_tile.get('value'))} -> {_format_value(curr_tile.get('value'))}"
                )
            if prev_tile.get("percent") != curr_tile.get("percent"):
                lines.append(
                    f"📈 {key} % : {_format_value(prev_tile.get('percent'))} -> {_format_value(curr_tile.get('percent'))}"
                )
        elif curr_tile:
            lines.append(
                f"🆕 {key} : - -> {_format_value(curr_tile.get('value'))}"
            )
        elif prev_tile:
            lines.append(
                f"❌ {key} : {_format_value(prev_tile.get('value'))} -> -"
            )
    return lines


def _summarize_table_changes(prev_tables: List[dict], curr_tables: List[dict]) -> List[str]:
    lines: List[str] = []
    prev_heads = [t.get("heading") for t in prev_tables]
    curr_heads = [t.get("heading") for t in curr_tables]
    if prev_heads != curr_heads:
        lines.append(
            f"🗂️ Tableaux : {_stringify_value(prev_heads, 80)} -> {_stringify_value(curr_heads, 80)}"
        )

    prev_map = _build_label_map(prev_tables, "heading", "Table")
    curr_map = _build_label_map(curr_tables, "heading", "Table")
    keys = list(curr_map.keys()) + [k for k in prev_map if k not in curr_map]
    for key in keys:
        prev_table = prev_map.get(key)
        curr_table = curr_map.get(key)
        if prev_table and curr_table:
            prev_rows = len(prev_table.get("rows", []))
            curr_rows = len(curr_table.get("rows", []))
            if prev_rows != curr_rows:
                lines.append(f"📄 {key} : {prev_rows} lignes -> {curr_rows} lignes")
        elif curr_table:
            curr_rows = len(curr_table.get("rows", []))
            lines.append(f"📄 {key} : 0 lignes -> {curr_rows} lignes")
        elif prev_table:
            prev_rows = len(prev_table.get("rows", []))
            lines.append(f"📄 {key} : {prev_rows} lignes -> 0 lignes")
    return lines


def _first_diff(prev: Any, curr: Any, path: str = "") -> tuple[str, Any, Any] | None:
    if isinstance(prev, dict) and isinstance(curr, dict):
        keys = sorted(set(prev) | set(curr), key=str)
        for key in keys:
            new_path = f"{path}.{key}" if path else str(key)
            if key not in prev:
                return new_path, None, curr[key]
            if key not in curr:
                return new_path, prev[key], None
            diff = _first_diff(prev[key], curr[key], new_path)
            if diff:
                return diff
        return None

    if isinstance(prev, list) and isinstance(curr, list):
        max_len = max(len(prev), len(curr))
        for idx in range(max_len):
            new_path = f"{path}[{idx}]" if path else f"[{idx}]"
            if idx >= len(prev):
                return new_path, None, curr[idx]
            if idx >= len(curr):
                return new_path, prev[idx], None
            diff = _first_diff(prev[idx], curr[idx], new_path)
            if diff:
                return diff
        return None

    if prev != curr:
        return path or "root", prev, curr
    return None


def summarize_changes(prev: dict | None, curr: dict) -> str:
    if prev is None:
        return "Première capture enregistrée."

    lines: List[str] = []
    lines.extend(_summarize_tile_changes(prev.get("tiles", []), curr.get("tiles", [])))
    lines.extend(_summarize_table_changes(prev.get("tables", []), curr.get("tables", [])))

    if prev.get("user_id") != curr.get("user_id"):
        lines.append(
            f"👤 user_id : {_format_value(prev.get('user_id'))} -> {_format_value(curr.get('user_id'))}"
        )

    # Always surface the first value-level delta so the notification shows a before/after,
    # even when higher-level counters (row counts, tiles) already generated lines.
    diff = _first_diff(prev, curr)
    if diff:
        path, before, after = diff
        diff_line = f"Δ {path} : {_format_value(before)} -> {_format_value(after)}"
        if diff_line not in lines:
            lines.insert(0, diff_line)
    elif not lines:
        lines.append("Changements détectés.")

    return "\n".join(lines[:7])


def build_notification_message(summary: str, snap_path: Path | None) -> str:
    """Normalize and trim the notification payload shown by Pushover."""
    summary_lines = [line.strip() for line in summary.splitlines() if line.strip()]
    if not summary_lines:
        summary_lines = ["Changement détecté (détails indisponibles)."]
    if snap_path is not None:
        summary_lines.append(f"📁 {snap_path.name}")
    message = "\n".join(summary_lines)
    return message[:1024]  # Pushover message limit is 1024 chars


def _atomic_dump_json(path: Path, data: dict, gzip_compress: bool = False) -> None:
    """Write JSON atomically to avoid half-written snapshots."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    if gzip_compress:
        with gzip.open(tmp_path, "wt", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    else:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _is_login_page(html: str) -> bool:
    """Heuristic to detect if the login page was returned (failed credentials)."""
    return "login[username]" in html and "login[password]" in html


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch LAYA dashboard.")
    parser.add_argument(
        "--simulate-change",
        action="store_true",
        help="Force un changement fictif pour tester la notif",
    )
    args = parser.parse_args()

    load_env_file()

    username = os.getenv("PORTAD_USER")
    password = os.getenv("PORTAD_PASS")
    if not username or not password:
        raise SystemExit("PORTAD_USER / PORTAD_PASS manquants (dans .env).")

    session: requests.Session | None = None
    try:
        session = build_session()

        # 1) Login once
        landing_html = login(session, username, password)

        # 2) Get a page that contains the user id (if not already present)
        user_id = extract_user_id(landing_html)
        if _is_login_page(landing_html) and not user_id:
            raise RuntimeError("Login failed: formulaire de connexion renvoyé.")
        if not user_id:
            display_page = session.get(DISPLAY_TABLEAU_PAGE, timeout=HTTP_TIMEOUT)
            display_page.raise_for_status()
            user_id = extract_user_id(display_page.text)
            if _is_login_page(display_page.text) and not user_id:
                raise RuntimeError(
                    "Login failed: toujours sur la page de connexion après authentification."
                )

        if not user_id:
            raise RuntimeError("Could not determine id_person_conn after login.")

        # 3) Fetch the tableau HTML fragment in one request
        tableau_html = fetch_dashboard_html(session, user_id)

        # 4) Parse with BeautifulSoup
        soup = BeautifulSoup(tableau_html, "lxml")
        data = {
            "user_id": user_id,
            "tiles": parse_tile_counters(soup),
            "tables": parse_two_col_tables(soup),
        }

        if args.simulate_change:
            data["__simulated_change"] = datetime.now().isoformat()

        # Diff & notify
        previous = load_last_snapshot()
        changed = previous is None or diff_changed(previous, data)
        snap_path = None
        if changed:
            snap_path = save_snapshot(data)
            cleanup_old_snapshots()
            if previous is not None:
                summary = summarize_changes(previous, data)
                message = build_notification_message(summary, snap_path)
                send_pushover(message, title="📈 Portad: changement détecté")
        else:
            # keep last_snapshot as-is; ensure at least baseline exists
            if previous is None:
                save_snapshot(data)

        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        notify_error(exc)
        return 1
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - simple CLI guard
        sys.stderr.write(f"Error: {exc}\n")
        raise SystemExit(1)
