from __future__ import annotations

import argparse

from .config import get_settings
from .db import Database


def main() -> None:
    parser = argparse.ArgumentParser(prog="notion2local")
    parser.add_argument("command", choices=["init-db"], nargs="?", default="init-db")
    args = parser.parse_args()
    if args.command == "init-db":
        database = Database(get_settings())
        database.create_all()
        database.dispose()
        print("database schema initialized")
