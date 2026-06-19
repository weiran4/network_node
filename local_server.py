from __future__ import annotations

import json
import importlib.util
import mimetypes
import os
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "4177"))


def ensure_runtime_dependencies() -> None:
    missing = []
    if importlib.util.find_spec("sympy") is None:
        missing.append("sympy")

    if not missing:
        return

    print(f"Installing missing Python package(s): {', '.join(missing)}")
    pip_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    wheels_dir = ROOT / "wheels"
    if wheels_dir.exists():
        pip_command.extend(["--no-index", "--find-links", str(wheels_dir)])
    pip_command.extend(missing)

    try:
        subprocess.check_call(pip_command)
    except subprocess.CalledProcessError as exc:
        packages = " ".join(missing)
        offline_hint = (
            f"\nA local wheels folder was found at {wheels_dir}, but installation still failed."
            if wheels_dir.exists()
            else "\nIf this computer is offline, copy a wheels folder prepared with: python -m pip download sympy -d wheels"
        )
        raise SystemExit(
            "Unable to install required Python packages automatically.\n"
            f"{offline_hint}\n"
            f"Please run this command manually:\n\n    {sys.executable} -m pip install {packages}\n"
        ) from exc


def safe_file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name or "branch-builder-circuit.json"))


def run_python_json(script_name: str, payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / script_name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        timeout=600,
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stderr or str(exc)) from exc
    if proc.returncode != 0 or data.get("ok") is False:
        raise RuntimeError(data.get("error") or proc.stderr or f"{script_name} failed")
    return data


def run_save_as_dialog(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "save_as_dialog.py")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(ROOT),
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(proc.stderr or str(exc)) from exc
    if proc.returncode != 0:
        raise RuntimeError(data.get("error") or proc.stderr or "Save As dialog failed")
    return data


class BranchBuilderHandler(SimpleHTTPRequestHandler):
    server_version = "BranchBuilderPython/1.0"

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        clean = parsed.path
        if clean == "/":
            clean = "/index.html"
        target = (ROOT / clean.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT)):
            return str(ROOT / "index.html")
        return str(target)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/reduce-system":
                self.write_json(200, run_python_json("reduce_api.py", self.read_json()))
                return
            if parsed.path == "/optimized-elimination":
                self.write_json(200, run_python_json("optimized_elimination_api.py", self.read_json()))
                return
            if parsed.path == "/multi-case-c-export":
                payload = self.read_json()
                payload["mode"] = "multi_case_c_export"
                self.write_json(200, run_python_json("optimized_elimination_api.py", payload))
                return
            if parsed.path == "/validate-blackbox-observers":
                self.write_json(200, run_python_json("blackbox_validation_api.py", self.read_json()))
                return
            if parsed.path == "/save-circuit":
                payload = self.read_json()
                exports_dir = ROOT / "exports"
                exports_dir.mkdir(exist_ok=True)
                file_path = exports_dir / safe_file_name(payload.get("filename"))
                data = str(payload.get("data") or "")
                file_path.write_text(data, encoding="utf-8")
                self.write_json(200, {"ok": True, "path": str(file_path), "bytes": len(data.encode("utf-8"))})
                return
            if parsed.path == "/save-circuit-as":
                self.write_json(200, run_save_as_dialog(self.read_json()))
                return
            self.write_json(404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/list-circuits":
            exports_dir = ROOT / "exports"
            exports_dir.mkdir(exist_ok=True)
            files = []
            for file_path in exports_dir.glob("*.json"):
                stat = file_path.stat()
                files.append(
                    {
                        "name": file_path.name,
                        "size": stat.st_size,
                        "modified": __import__("datetime").datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    }
                )
            files.sort(key=lambda item: item["modified"], reverse=True)
            self.write_json(200, {"files": files})
            return
        return super().do_GET()

    def guess_type(self, path: str) -> str:
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        if path.endswith(".js"):
            return "text/javascript; charset=utf-8"
        if path.endswith(".json"):
            return "application/json; charset=utf-8"
        return mimetypes.guess_type(path)[0] or "text/plain; charset=utf-8"


if __name__ == "__main__":
    ensure_runtime_dependencies()
    os.chdir(ROOT)
    server = ThreadingHTTPServer((HOST, PORT), BranchBuilderHandler)
    print(f"Branch Builder running at http://{HOST}:{PORT}/")
    server.serve_forever()
