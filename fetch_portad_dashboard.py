#!/usr/bin/env python3
"""
Fetch and parse the LAYA dashboard (display-tableau) once using requests + BeautifulSoup.

Usage:
    PORTAD_USER=you@example.com PORTAD_PASS=secret .venv/bin/python fetch_portad_dashboard.py

Defaults fall back to the credentials provided by the user for convenience.
"""

from __future__ import annotations

import json
import os
import sys
import gzip
from typing import Dict, List

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pathlib import Path
from collections import deque


BASE_URL = "https://portad.laya.fr/"
LOGIN_URL = BASE_URL + "?ext=loginpage&controller=ext&action=login"
DISPLAY_TABLEAU_PAGE = BASE_URL + "index.php?new=1&id=display-tableau"
AJAX_PERSON_URL = BASE_URL + "index.php?ext=contact&controller=person"

# Fallback credentials; prefer using environment variables to avoid hard‑coding secrets.
DEFAULT_USER = "aleqsd@gmail.com"
DEFAULT_PASS = "kHq3vW54%&jbqhuNBA"

# Snapshot storage
SNAPSHOT_DIR = Path("snapshots")
LAST_SNAPSHOT = SNAPSHOT_DIR / "last_snapshot.json"
SNAPSHOT_RETENTION = 30  # keep last 30 gzip snapshots


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


def login(session: requests.Session, username: str, password: str) -> str:
    """Perform a single login and return the HTML of the landing page."""
    # Prime session with initial GET to set cookies
    session.get(BASE_URL, timeout=20)

    payload = {
        "login[username]": username,
        "login[password]": password,
        "login[submit]": "Se connecter",
        "rememberme": "forever",
        "login[redirectURL]": "",
        "login[ope]": "",
        "login[isMobileApp]": "",
    }

    resp = session.post(LOGIN_URL, data=payload, allow_redirects=True, timeout=20)
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
    resp = session.post(AJAX_PERSON_URL, data=payload, headers=headers, timeout=20)
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
    with gzip.open(snap_path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    with LAST_SNAPSHOT.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
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
        requests.post(
            "https://api.pushover.net/1/messages.json", data=payload, timeout=10
        )
    except Exception:
        # non-fatal
        pass


def notify_error(exc: Exception) -> None:
    msg = f"Echec Portad: {exc}"
    send_pushover(msg, title="Portad dashboard ERROR")
    sys.stderr.write(msg + "\n")


def main() -> int:
    load_env_file()

    username = os.getenv("PORTAD_USER", DEFAULT_USER)
    password = os.getenv("PORTAD_PASS", DEFAULT_PASS)
    if not username or not password:
        raise SystemExit("PORTAD_USER / PORTAD_PASS manquants (dans .env).")

    try:
        with requests.Session() as session:
            # 1) Login once
            landing_html = login(session, username, password)

            # 2) Get a page that contains the user id (if not already present)
            user_id = extract_user_id(landing_html)
            if not user_id:
                display_page = session.get(DISPLAY_TABLEAU_PAGE, timeout=20)
                display_page.raise_for_status()
                user_id = extract_user_id(display_page.text)

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

            # Diff & notify
            previous = load_last_snapshot()
            changed = previous is None or diff_changed(previous, data)
            snap_path = None
            if changed:
                snap_path = save_snapshot(data)
                cleanup_old_snapshots()
                if previous is not None:
                    send_pushover(
                        f"Changement détecté sur le tableau de bord. Snapshot: {snap_path.name}"
                    )
            else:
                # keep last_snapshot as-is; ensure at least baseline exists
                if previous is None:
                    save_snapshot(data)

            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        notify_error(exc)
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - simple CLI guard
        sys.stderr.write(f"Error: {exc}\n")
        raise SystemExit(1)
