#!/usr/bin/env python3


###
# FedWatcher Weight Management Script
#
# Purpose:
# This script manages the sentiment scoring weights stored in the SQLite
# database table `weights`.
#
# The `weights` table controls how much importance the analyst agent gives
# to each analytical dimension for each document type.
#
# Example structure:
#   statement:
#       forward_guidance = 0.45
#       inflation        = 0.25
#       labor_market     = 0.15
#       general          = 0.15
#
#   minutes:
#       forward_guidance  = 0.40
#       policy_discussion = 0.25
#       inflation         = 0.20
#       labor_market      = 0.15
#
#   speech:
#       forward_guidance = 0.35
#       inflation        = 0.25
#       labor_market     = 0.20
#       general          = 0.20
#
# Main commands:
#
# 1. Insert or reset default weights:
#       python3 scripts/update_weights.py reset-defaults
#
# 2. Show current weights:
#       python3 scripts/update_weights.py show
#
# 3. Validate that weights sum to 1.0 for each document type:
#       python3 scripts/update_weights.py validate
#
# 4. Change one specific weight:
#       python3 scripts/update_weights.py set \
#           --doc-type statement \
#           --dimension forward_guidance \
#           --weight 0.50
#
# 5. Use a custom database path:
#       python3 scripts/update_weights.py --db fedwatcher.db show
#
# Important:
# After changing one weight, make sure the total weights for that document
# type still sum to 1.0. Run:
#       python3 scripts/update_weights.py validate
#
# Example adjustment:
# If you increase statement.forward_guidance from 0.45 to 0.50,
# you should reduce another statement weight, for example:
#       python3 scripts/update_weights.py set \
#           --doc-type statement \
#           --dimension general \
#           --weight 0.10
#
# Database design:
# The database is the source of truth. The analyst agent should load the
# latest weights from the `weights` table during each run instead of using
# a hardcoded Python dictionary.
#
# Future website integration:
# A dashboard or FastAPI endpoint can update the same `weights` table.
# The analyst agent will then automatically use the latest configured
# weights on its next run.
###

import argparse
import sqlite3
from typing import Dict


DB_PATH_DEFAULT = "fedwatcher.db"

DEFAULT_WEIGHTS = {
    "statement": {
        "forward_guidance": 0.45,
        "inflation": 0.25,
        "labor_market": 0.15,
        "general": 0.15,
    },
    "speech": {
        "forward_guidance": 0.35,
        "inflation": 0.25,
        "labor_market": 0.20,
        "general": 0.20,
    },
}


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_weights_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,
            dimension TEXT NOT NULL,
            weight REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(doc_type, dimension)
        );
    """)
    conn.commit()


def get_weights(conn: sqlite3.Connection) -> Dict[str, Dict[str, float]]:
    rows = conn.execute("""
        SELECT doc_type, dimension, weight
        FROM weights
        ORDER BY doc_type, dimension;
    """).fetchall()

    result: Dict[str, Dict[str, float]] = {}

    for row in rows:
        doc_type = row["doc_type"]
        dimension = row["dimension"]
        weight = float(row["weight"])

        if doc_type not in result:
            result[doc_type] = {}

        result[doc_type][dimension] = weight

    return result


def print_weights(conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT doc_type, dimension, weight
        FROM weights
        ORDER BY
            CASE doc_type
                WHEN 'statement' THEN 1
                WHEN 'speech' THEN 2
                ELSE 3
            END,
            dimension;
    """).fetchall()

    if not rows:
        print("No weights found.")
        return

    current_doc_type = None
    total = 0.0

    for row in rows:
        doc_type = row["doc_type"]
        dimension = row["dimension"]
        weight = float(row["weight"])

        if current_doc_type is not None and doc_type != current_doc_type:
            print(f"  TOTAL: {total:.4f}")
            print()
            total = 0.0

        if doc_type != current_doc_type:
            current_doc_type = doc_type
            print(f"{doc_type.upper()}")

        print(f"  {dimension:<20} {weight:.4f}")
        total += weight

    print(f"  TOTAL: {total:.4f}")


def validate_weights(conn: sqlite3.Connection, tolerance: float = 0.0001) -> bool:
    rows = conn.execute("""
        SELECT doc_type, ROUND(SUM(weight), 6) AS total_weight
        FROM weights
        GROUP BY doc_type
        ORDER BY doc_type;
    """).fetchall()

    ok = True

    for row in rows:
        doc_type = row["doc_type"]
        total = float(row["total_weight"])

        if abs(total - 1.0) > tolerance:
            print(f"[ERROR] {doc_type} weights sum to {total:.6f}, expected 1.000000")
            ok = False
        else:
            print(f"[OK] {doc_type} weights sum to {total:.6f}")

    return ok


def reset_defaults(conn: sqlite3.Connection) -> None:
    for doc_type, dimensions in DEFAULT_WEIGHTS.items():
        for dimension, weight in dimensions.items():
            conn.execute("""
                INSERT INTO weights (doc_type, dimension, weight, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(doc_type, dimension)
                DO UPDATE SET
                    weight = excluded.weight,
                    updated_at = CURRENT_TIMESTAMP;
            """, (doc_type, dimension, weight))

    conn.commit()
    print("Default weights inserted/updated.")


def update_weight(
    conn: sqlite3.Connection,
    doc_type: str,
    dimension: str,
    weight: float,
) -> None:
    if weight < 0 or weight > 1:
        raise ValueError("Weight must be between 0 and 1.")

    conn.execute("""
        INSERT INTO weights (doc_type, dimension, weight, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(doc_type, dimension)
        DO UPDATE SET
            weight = excluded.weight,
            updated_at = CURRENT_TIMESTAMP;
    """, (doc_type, dimension, weight))

    conn.commit()
    print(f"Updated: {doc_type}.{dimension} = {weight:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage FedWatcher sentiment weights."
    )

    parser.add_argument(
        "--db",
        default=DB_PATH_DEFAULT,
        help="SQLite database path. Default: fedwatcher.db",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show", help="Show current weights.")
    subparsers.add_parser("validate", help="Validate weights sum to 1.0 by doc_type.")
    subparsers.add_parser("reset-defaults", help="Insert/update default weights.")

    set_parser = subparsers.add_parser("set", help="Set one weight.")
    set_parser.add_argument("--doc-type", required=True, choices=["statement", "speech"])
    set_parser.add_argument("--dimension", required=True)
    set_parser.add_argument("--weight", required=True, type=float)

    args = parser.parse_args()

    conn = connect(args.db)

    try:
        ensure_weights_table(conn)

        if args.command == "show":
            print_weights(conn)

        elif args.command == "validate":
            ok = validate_weights(conn)
            if not ok:
                raise SystemExit(1)

        elif args.command == "reset-defaults":
            reset_defaults(conn)
            validate_weights(conn)

        elif args.command == "set":
            update_weight(
                conn=conn,
                doc_type=args.doc_type,
                dimension=args.dimension,
                weight=args.weight,
            )
            validate_weights(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
