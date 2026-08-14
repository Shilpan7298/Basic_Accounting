"""Entry point for the packaged Windows/macOS build.

The same binary is both the server and the workstation launcher — which mode it
runs in is chosen at install time, so it does not matter whether the office
host turns out to be a Windows box or a Mac.

    urjapod serve            run the server (what the office host does)
    urjapod open             just open a browser at the configured server
    urjapod create-owner     first-run account, from the command line
    urjapod backup           write a timestamped copy of the database

Everything writes under a per-user data directory, never next to the
executable: Program Files is not writable by a normal account, and a database
inside it would fail on the first invoice.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

DEFAULT_PORT = 8765
APP_NAME = "Urjapod"


def data_dir() -> Path:
    """Where the database, uploads and generated documents live."""
    if override := os.environ.get("URJAPOD_DATA_DIR"):
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("PROGRAMDATA", Path.home())) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def _configure_environment() -> Path:
    """Point the app at the data directory before anything imports settings."""
    root = data_dir()
    (root / "storage").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("STORAGE_DIR", str(root / "storage"))
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{root / 'urjapod.db'}")
    return root


def _lan_hostname() -> str:
    """A name the accountant's machine can actually reach."""
    try:
        return socket.gethostname()
    except OSError:
        return "localhost"


def _wait_until_up(port: int, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def cmd_serve(args: argparse.Namespace) -> int:
    root = _configure_environment()

    import uvicorn

    from .db import engine
    from .models import Base

    # Alembic owns the schema in a git checkout; in a frozen build the
    # migration scripts may not be present, so create_all keeps a fresh
    # install working. Both are idempotent.
    Base.metadata.create_all(engine)

    host = "0.0.0.0" if args.lan else "127.0.0.1"
    url = f"http://{_lan_hostname()}:{args.port}" if args.lan else f"http://127.0.0.1:{args.port}"

    print(f"{APP_NAME} — data in {root}")
    print(f"  this machine : http://127.0.0.1:{args.port}")
    if args.lan:
        print(f"  other machines: {url}")
        print("  (if they cannot reach it, allow the port through the firewall)")

    if args.open_browser:
        threading.Thread(
            target=lambda: _wait_until_up(args.port) and webbrowser.open(url), daemon=True
        ).start()

    uvicorn.run("app.main:app", host=host, port=args.port, log_level="info", access_log=False)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Workstation mode: the server is elsewhere, just open a browser at it."""
    target = args.server or os.environ.get("URJAPOD_SERVER") or f"http://127.0.0.1:{args.port}"
    if not target.startswith("http"):
        target = f"http://{target}"
    print(f"opening {target}")
    webbrowser.open(target)
    return 0


def cmd_create_owner(args: argparse.Namespace) -> int:
    _configure_environment()

    from getpass import getpass

    from .db import SessionLocal, engine
    from .models import Base, Role, User
    from .services import security
    from .services.audit import install_session_listener

    install_session_listener(SessionLocal)
    Base.metadata.create_all(engine)

    db = SessionLocal()
    try:
        if db.query(User).count() and not args.force:
            print("users already exist; use the web interface to add more")
            return 1
        username = args.username or input("username: ").strip()
        full_name = args.full_name or input("full name: ").strip()
        password = args.password or getpass("password: ")
        if not args.password:
            if password != getpass("repeat password: "):
                print("passwords do not match")
                return 1
        security.create_user(
            db, username=username, full_name=full_name, password=password,
            role=Role.OWNER, actor="setup", must_change_password=False,
        )
        db.commit()
    except (ValueError, security.WeakPassword) as exc:
        print(f"could not create the account: {exc}")
        return 1
    finally:
        db.close()

    print(f"owner account '{username}' created")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """A copy of the books, safe to take while the server is running."""
    import shutil
    from datetime import datetime

    root = _configure_environment()
    target_dir = Path(args.to) if args.to else root / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    database = root / "urjapod.db"
    if database.exists():
        destination = target_dir / f"urjapod-{stamp}.db"
        # sqlite3's backup API is consistent under concurrent writes; a plain
        # file copy of a live WAL database is not.
        import sqlite3

        source = sqlite3.connect(str(database))
        try:
            with sqlite3.connect(str(destination)) as copy:
                source.backup(copy)
        finally:
            source.close()
        print(f"database  -> {destination}")

    documents = root / "storage"
    if documents.exists():
        archive = shutil.make_archive(str(target_dir / f"documents-{stamp}"), "zip", documents)
        print(f"documents -> {archive}")

    print("\nKeep a copy off this machine. A backup on the same disk is not a backup.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="urjapod", description=f"{APP_NAME} orders & invoicing")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the server")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument(
        "--lan", action="store_true",
        help="accept connections from other machines (the office host wants this)",
    )
    serve.add_argument("--no-open", dest="open_browser", action="store_false", default=True)
    serve.set_defaults(func=cmd_serve)

    open_cmd = sub.add_parser("open", help="open a browser at the server")
    open_cmd.add_argument("--server", help="e.g. http://urjapod-server:8765")
    open_cmd.add_argument("--port", type=int, default=DEFAULT_PORT)
    open_cmd.set_defaults(func=cmd_open)

    owner = sub.add_parser("create-owner", help="create the first owner account")
    owner.add_argument("--username")
    owner.add_argument("--full-name")
    owner.add_argument("--password", help="omit to be prompted (safer — it stays out of history)")
    owner.add_argument("--force", action="store_true")
    owner.set_defaults(func=cmd_create_owner)

    backup = sub.add_parser("backup", help="back up the database and documents")
    backup.add_argument("--to", help="destination folder")
    backup.set_defaults(func=cmd_backup)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        # Double-clicking the executable should do the obvious thing.
        args = parser.parse_args(["serve"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
