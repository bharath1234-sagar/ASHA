"""Securely delete all saved visit records (use after judging).

  python scripts/delete_all_data.py            # asks for confirmation
  python scripts/delete_all_data.py --yes      # no prompt
  python scripts/delete_all_data.py --yes --remove-file   # also delete the database file
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.config import ConfigError, load_config  # noqa: E402
from backend.db import VisitDB  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--yes", action="store_true")
ap.add_argument("--remove-file", action="store_true")
a = ap.parse_args()
try:
    cfg = load_config()
except ConfigError as e:
    sys.exit(str(e))
db = VisitDB(cfg.db_path, cfg.encryption_key)
n = db.count()
if not a.yes and input(f"Delete {n} saved record(s) from {cfg.db_path}? Type DELETE to confirm: ") != "DELETE":
    sys.exit("Cancelled. Nothing deleted.")
print(f"Deleted {db.delete_all(remove_file=a.remove_file)} record(s)." + (" Database file removed." if a.remove_file else ""))
