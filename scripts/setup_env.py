"""Creates .env from .env.example with a fresh encryption key. Safe to re-run (never overwrites)."""
import shutil
import sys
from pathlib import Path

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parent.parent
env, example = root / ".env", root / ".env.example"
if env.exists():
    print(".env already exists - left unchanged. Open it in VS Code to edit.")
    sys.exit(0)
shutil.copy(example, env)
text = env.read_text(encoding="utf-8").replace("VFR_ENCRYPTION_KEY=", "VFR_ENCRYPTION_KEY=" + Fernet.generate_key().decode(), 1)
env.write_text(text, encoding="utf-8")
print("Created .env with a new encryption key. Back this key up: without it saved records cannot be read.")
