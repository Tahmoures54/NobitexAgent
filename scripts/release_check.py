"""Release validation for CryptoScanner."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".env", "app.db", "app.db-shm", "app.db-wal"}


def main() -> int:
    errors = []
    for path in ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"Syntax error: {path}: {exc}")

    for path in ROOT.rglob("*"):
        if path.is_file() and (path.name in FORBIDDEN or "__pycache__" in path.parts or path.suffix == ".pyc"):
            errors.append(f"Runtime/secret artifact must not be released: {path.relative_to(ROOT)}")

    example = ROOT / "configs_nobitex_irt.example.json"
    try:
        json.loads(example.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"Invalid Nobitex example config: {exc}")

    if errors:
        print("RELEASE CHECK FAILED")
        print("\n".join(f"- {e}" for e in errors))
        return 1
    print("RELEASE CHECK PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
