import os
import json
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Union, Optional

from flask import Flask, jsonify, render_template, request
import threading
import time
import uuid


def _get_base_path() -> Path:
    # When packaged with PyInstaller, resources are in sys._MEIPASS
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


BASE_PATH = _get_base_path()
app = Flask(
    __name__,
    static_folder=str(BASE_PATH / "static"),
    template_folder=str(BASE_PATH / "templates"),
)


def is_hidden(path: Path) -> bool:
    try:
        name = path.name
        if not name:
            return False
        if name.startswith("."):
            return True
        # Windows specific hidden attribute check
        if os.name == "nt":
            import ctypes
            FILE_ATTRIBUTE_HIDDEN = 0x02
            FILE_ATTRIBUTE_SYSTEM = 0x04
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs == -1:
                return False
            return bool(attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))
        return False
    except Exception:
        return False


def safe_stat(path: Path, follow_symlinks: bool) -> Optional[os.stat_result]:
    try:
        return path.stat(follow_symlinks=follow_symlinks)
    except Exception:
        return None


def scan_directory(
    root_path: Path,
    max_depth: int = 50,
    follow_symlinks: bool = False,
    exclude_hidden: bool = True,
    _depth: int = 0,
    _visited_realpaths: Optional[set] = None,
    _cancel_event: Optional[threading.Event] = None,
    _progress: Optional[dict] = None,
    _report_every: int = 100,
) -> Dict[str, Union[str, int, List[dict]]]:
    if _visited_realpaths is None:
        _visited_realpaths = set()

    node: Dict[str, Union[str, int, List[dict]]] = {
        "name": root_path.name or str(root_path),
        "path": str(root_path),
        "size": 0,
    }

    # Avoid cycles via realpath
    try:
        real = os.path.realpath(root_path)
        if real in _visited_realpaths:
            node["note"] = "skipped_cycle"
            return node
        _visited_realpaths.add(real)
    except Exception:
        pass

    # Check for cancel
    if _cancel_event is not None and _cancel_event.is_set():
        return {"name": root_path.name or str(root_path), "path": str(root_path), "size": 0, "note": "canceled"}

    st = safe_stat(root_path, follow_symlinks=follow_symlinks)
    if st is None:
        node["note"] = "stat_failed"
        return node

    # File
    if not root_path.is_dir():
        size_val = int(st.st_size)
        node["size"] = size_val
        if _progress is not None:
            _progress["files"] += 1
            _progress["bytes"] += size_val
        return node

    # Directory
    children: List[dict] = []
    total_size = 0

    if _depth >= max_depth:
        # At max depth, approximate by directory entry size if available
        node["size"] = int(st.st_size)
        node["note"] = "max_depth_reached"
        return node

    try:
        with os.scandir(root_path) as it:
            processed = 0
            for entry in it:
                try:
                    entry_path = Path(entry.path)

                    if exclude_hidden and is_hidden(entry_path):
                        continue

                    # Avoid following links unless requested
                    if entry.is_symlink():
                        if not follow_symlinks:
                            continue

                    if _progress is not None:
                        _progress["current"] = str(entry_path)

                    if _cancel_event is not None and _cancel_event.is_set():
                        node["note"] = "canceled"
                        return node

                    if entry.is_dir(follow_symlinks=follow_symlinks):
                        child = scan_directory(
                            entry_path,
                            max_depth=max_depth,
                            follow_symlinks=follow_symlinks,
                            exclude_hidden=exclude_hidden,
                            _depth=_depth + 1,
                            _visited_realpaths=_visited_realpaths,
                            _cancel_event=_cancel_event,
                            _progress=_progress,
                            _report_every=_report_every,
                        )
                        total_size += int(child.get("size", 0))
                        children.append(child)
                    else:
                        est = safe_stat(entry_path, follow_symlinks=follow_symlinks)
                        file_size = int(est.st_size) if est else 0
                        total_size += file_size
                        children.append(
                            {
                                "name": entry.name,
                                "path": str(entry_path),
                                "size": file_size,
                            }
                        )
                        if _progress is not None:
                            _progress["files"] += 1
                            _progress["bytes"] += file_size
                    processed += 1
                    if _progress is not None and processed % _report_every == 0:
                        _progress["updated_at"] = time.time()
                except (PermissionError, FileNotFoundError):
                    continue
                except Exception:
                    # Best-effort scanning; skip problematic entries
                    continue
    except (PermissionError, FileNotFoundError):
        # Cannot list directory; fallback to its own size
        node["size"] = int(st.st_size)
        node["note"] = "unreadable_directory"
        return node

    # Sort children by size descending
    children.sort(key=lambda c: int(c.get("size", 0)), reverse=True)

    node["children"] = children
    node["size"] = total_size
    return node


class ScanManager:
    def __init__(self):
        self._scans: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, path: str, max_depth: int, follow_symlinks: bool, exclude_hidden: bool) -> str:
        scan_id = str(uuid.uuid4())
        cancel_event = threading.Event()
        progress = {
            "state": "running",
            "bytes": 0,
            "files": 0,
            "current": path,
            "started_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
        }

        def _run():
            try:
                tree = scan_directory(
                    Path(path),
                    max_depth=max_depth,
                    follow_symlinks=follow_symlinks,
                    exclude_hidden=exclude_hidden,
                    _cancel_event=cancel_event,
                    _progress=progress,
                )
                if cancel_event.is_set():
                    progress["state"] = "canceled"
                else:
                    progress["state"] = "done"
                    progress["result"] = tree
            except Exception as exc:
                progress["state"] = "error"
                progress["error"] = str(exc)
            finally:
                progress["updated_at"] = time.time()

        t = threading.Thread(target=_run, daemon=True)
        with self._lock:
            self._scans[scan_id] = {
                "thread": t,
                "cancel": cancel_event,
                "progress": progress,
            }
        t.start()
        return scan_id

    def status(self, scan_id: str) -> Optional[dict]:
        with self._lock:
            entry = self._scans.get(scan_id)
            if not entry:
                return None
            p = entry["progress"].copy()
            # Do not clone result deeply; fine to return reference for JSON
            return p

    def cancel(self, scan_id: str) -> bool:
        with self._lock:
            entry = self._scans.get(scan_id)
            if not entry:
                return False
            entry["cancel"].set()
            return True


scan_manager = ScanManager()


def list_windows_drives() -> List[str]:
    if os.name != "nt":
        return []
    import string
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = []
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                drives.append(f"{letter}:\\")
        return drives
    except Exception:
        # Fallback: common drives
        return [f"{l}:\\" for l in ["C", "D", "E", "F"]]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/drive_roots")
def api_drive_roots():
    return jsonify({"drives": list_windows_drives()})


@app.post("/api/scan/start")
def api_scan_start():
    try:
        body = request.get_json(force=True, silent=True) or {}
        path_str: str = body.get("path") or "C:\\"
        max_depth: int = int(body.get("max_depth", 50))
        follow_symlinks: bool = bool(body.get("follow_symlinks", False))
        exclude_hidden: bool = bool(body.get("exclude_hidden", True))

        path = Path(path_str)
        if not path.exists():
            return jsonify({"error": f"Path not found: {path_str}"}), 400

        scan_id = scan_manager.start(str(path), max_depth, follow_symlinks, exclude_hidden)
        return jsonify({"scan_id": scan_id})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.get("/api/scan/status")
def api_scan_status():
    scan_id = request.args.get("scan_id", "")
    status = scan_manager.status(scan_id)
    if status is None:
        return jsonify({"error": "scan_not_found"}), 404
    return jsonify(status)


@app.post("/api/scan/cancel")
def api_scan_cancel():
    try:
        body = request.get_json(force=True, silent=True) or {}
        scan_id: str = body.get("scan_id", "")
        ok = scan_manager.cancel(scan_id)
        if not ok:
            return jsonify({"error": "scan_not_found"}), 404
        return jsonify({"ok": True})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


@app.post("/api/browse_folder")
def api_browse_folder():
    # Open a native directory selection dialog and return the chosen path
    try:
        initial = (request.get_json(silent=True) or {}).get("initial")
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        path = filedialog.askdirectory(initialdir=initial or None, mustexist=True)
        root.destroy()
        if not path:
            return jsonify({"canceled": True})
        return jsonify({"path": path})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)


