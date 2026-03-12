"""
migrate.py
One-time migration script — run this ONCE on your local machine to add
the new tables needed for the Default Budget Template system.

Usage:
    python migrate.py

Safe to run multiple times — uses IF NOT EXISTS and INSERT OR IGNORE.
"""

import os
import sqlite3

DATABASE = os.path.join(os.path.dirname(__file__), 'instance', 'app.db')


def run():
    if not os.path.exists(DATABASE):
        print(f"ERROR: Database not found at {DATABASE}")
        print("Start the app once first (python app.py) to create it.")
        return

    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row

    print("Running migration...")

    # ── 1. Create new tables ──────────────────────────────────────────────────
    db.executescript('''
        CREATE TABLE IF NOT EXISTS default_category_budget (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            category TEXT    NOT NULL,
            amount   REAL    NOT NULL,
            UNIQUE(user_id, category)
        );

        CREATE TABLE IF NOT EXISTS monthly_category_budget (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            category TEXT    NOT NULL,
            month    INTEGER NOT NULL,
            year     INTEGER NOT NULL,
            amount   REAL    NOT NULL,
            UNIQUE(user_id, category, month, year)
        );
    ''')
    print("  ✓ Created tables: default_category_budget, monthly_category_budget")

    # ── 2. Migrate existing category_budget → default_category_budget ─────────
    old_rows = db.execute('SELECT * FROM category_budget').fetchall()
    migrated = 0
    for row in old_rows:
        db.execute(
            'INSERT OR IGNORE INTO default_category_budget (user_id, category, amount) '
            'VALUES (?, ?, ?)',
            (row['user_id'], row['category'], row['budget_amount'])
        )
        migrated += 1

    db.commit()

    if migrated:
        print(f"  ✓ Migrated {migrated} existing category budget(s) → default_category_budget")
    else:
        print("  ✓ No existing category budgets to migrate")

    # ── 3. Verify ─────────────────────────────────────────────────────────────
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    print(f"\nAll tables in DB: {tables}")

    dcb = db.execute('SELECT COUNT(*) FROM default_category_budget').fetchone()[0]
    mcb = db.execute('SELECT COUNT(*) FROM monthly_category_budget').fetchone()[0]
    print(f"default_category_budget rows : {dcb}")
    print(f"monthly_category_budget rows : {mcb}")

    db.close()
    print("\nMigration complete. You can now restart your Flask app.")


if __name__ == '__main__':
    run()
