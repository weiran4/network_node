from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    data = str(payload.get("data") or "")
    filename = str(payload.get("filename") or "branch-builder-circuit.json")
    if not filename.lower().endswith(".json"):
        filename += ".json"

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Save dialog unavailable: {exc}"}))
        return 0

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.asksaveasfilename(
            title="Save Circuit JSON",
            initialfile=filename,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
    finally:
        root.destroy()

    if not path:
        print(json.dumps({"ok": False, "canceled": True}))
        return 0

    file_path = Path(path)
    file_path.write_text(data, encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(file_path), "bytes": len(data.encode("utf-8"))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
