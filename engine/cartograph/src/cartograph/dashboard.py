"""
Local dashboard server for Cartograph.

Spins up a background HTTP server, serves a single-page registry browser,
and writes a PID file so it can be stopped later.
"""

import json
import os
import signal
import sys
import threading
import webbrowser
import http.server
import urllib.parse

def _state_dir():
    from .engine import _user_data_dir
    return os.path.join(_user_data_dir(), ".state")

def _pid_file():
    return os.path.join(_state_dir(), "dashboard.pid")

def _config_file():
    return os.path.join(_state_dir(), "dashboard.json")
_DEFAULT_PORT = 9473


def _load_config():
    cf = _config_file()
    if os.path.exists(cf):
        try:
            with open(cf, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_config(cfg):
    os.makedirs(_state_dir(), exist_ok=True)
    with open(_config_file(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def get_port():
    """Return the saved port or the default."""
    return _load_config().get("port", _DEFAULT_PORT)


def set_port(port):
    """Persist the dashboard port."""
    cfg = _load_config()
    cfg["port"] = port
    _save_config(cfg)


from .dashboard_ui import _HTML


def _make_handler(engine):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            qs = dict(urllib.parse.parse_qsl(parsed.query))

            if path == "/":
                self._send(200, "text/html", _HTML.encode())
            elif path == "/api/whoami":
                self._send_json(self._whoami())
            elif path == "/api/reload":
                try:
                    before = len(engine.widgets)
                    engine.reload()
                    after = len(engine.widgets)
                    self._send_json({"status": "ok", "before": before, "after": after})
                except Exception as e:
                    self._send_json({"status": "error", "error": str(e)})
            elif path == "/api/local":
                self._send_json(self._local())
            elif path == "/api/cloud":
                self._send_json(self._cloud())
            elif path == "/api/search":
                self._send_json(self._search(qs))
            elif path.startswith("/api/inspect/"):
                parts = path.removeprefix("/api/inspect/").split("/", 1)
                if len(parts) == 2:
                    self._send_json(self._inspect(parts[0], parts[1]))
                else:
                    self._send_json({"error": "Use /api/inspect/{owner}/{widget_id}"})
            elif path == "/api/search-local":
                self._send_json(self._search_local(qs))
            elif path == "/api/users/search":
                self._send_json(self._search_users(qs))
            elif path.startswith("/api/cloud-reviews/"):
                rest = path.removeprefix("/api/cloud-reviews/")
                parts = rest.split("/", 1)
                if len(parts) == 2:
                    self._send_json(self._cloud_reviews(parts[0], parts[1]))
                else:
                    self._send_json({"error": "Use /api/cloud-reviews/{owner}/{widget_id}"})
            elif path.startswith("/api/versions/"):
                widget_id = path.removeprefix("/api/versions/")
                self._send_json(self._versions(widget_id))
            elif path.startswith("/api/files/"):
                widget_id = path.removeprefix("/api/files/")
                self._send_json(self._files(widget_id, qs.get("owner", ""), qs.get("version", "")))
            else:
                self._send(404, "text/plain", b"Not found")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if path == "/api/review":
                self._send_json(self._add_review(body))
            elif path == "/api/cloud-review":
                self._send_json(self._add_cloud_review(body))
            else:
                self._send(404, "text/plain", b"Not found")

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if path == "/api/review":
                self._send_json(self._delete_review(body))
            else:
                self._send(404, "text/plain", b"Not found")

        def _delete_review(self, body):
            widget_id = body.get("widget_id", "")
            index = body.get("index", -1)
            if not widget_id or index < 0:
                return {"error": "widget_id and index are required"}
            widget = next((w for w in engine.widgets if w["id"] == widget_id), None)
            if not widget:
                return {"error": f"Widget '{widget_id}' not found"}
            review_path = os.path.join(widget["path"], "reviews.json")
            if not os.path.exists(review_path):
                return {"error": "No reviews file"}
            try:
                with open(review_path, encoding="utf-8") as f:
                    reviews_data = json.load(f)
                reviews = reviews_data.get("reviews", [])
                if index >= len(reviews):
                    return {"error": "Invalid review index"}
                reviews.pop(index)
                reviews_data["reviews"] = reviews
                with open(review_path, "w", encoding="utf-8") as f:
                    json.dump(reviews_data, f, indent=2)
                avg = round(sum(r["rating"] for r in reviews) / len(reviews), 1) if reviews else 0
                return {"status": "success", "avg_rating": avg}
            except Exception as e:
                return {"error": str(e)}

        def _add_cloud_review(self, body):
            owner = body.get("owner", "")
            widget_id = body.get("widget_id", "")
            score = body.get("score", 0)
            comment = body.get("comment", "")
            if not owner or not widget_id or not score:
                return {"error": "owner, widget_id, and score are required"}
            from .cloud import rate_widget
            result = rate_widget(owner, widget_id, int(score), comment)
            if "error" in result:
                return result
            result["author"] = ""
            try:
                from .auth import is_authenticated
                if is_authenticated():
                    from .cloud import whoami
                    profile = whoami()
                    result["author"] = profile.get("owner", "") or profile.get("username", "")
            except Exception:
                pass
            return result

        def _add_review(self, body):
            from .checkin import write_review
            widget_id = body.get("widget_id", "")
            score = body.get("score", 0)
            comment = body.get("comment", "")
            if not widget_id or not score:
                return {"error": "widget_id and score are required"}
            try:
                score = float(score)
                if not (1 <= score <= 5):
                    return {"error": "Score must be between 1 and 5"}
            except (ValueError, TypeError):
                return {"error": "Invalid score"}
            widget = next((w for w in engine.widgets if w["id"] == widget_id), None)
            if not widget:
                return {"error": f"Widget '{widget_id}' not found"}
            return write_review(widget["path"], score, widget.get("version", "unknown"), comment)

        def _whoami(self):
            from .auth import is_authenticated
            if not is_authenticated():
                return {"authenticated": False}
            from .cloud import whoami
            result = whoami()
            if "error" in result:
                return {"authenticated": False}
            return {"authenticated": True, **result}

        def _local(self):
            widgets = [
                {k: v for k, v in w.items() if k not in ("path", "implementation_hash")}
                for w in engine.widgets
            ]
            return {"widgets": widgets}

        def _cloud(self):
            from .auth import is_authenticated
            if not is_authenticated():
                return {"widgets": [], "skipped": "not authenticated"}
            from .cloud import list_widgets
            result = list_widgets()
            if "error" in result:
                return {"widgets": [], "error": result["error"]}
            return {"widgets": result.get("widgets", [])}

        def _search_local(self, qs):
            q = qs.get("q", "").strip()
            if not q:
                return {"widgets": [], "total": 0}
            domain = qs.get("domain")
            language = qs.get("language")
            result = engine._search_backend.query(q, domain_filter=domain, language_filter=language, top_k=20)
            widgets = result.get("results", [])
            return {"widgets": widgets, "total": len(widgets)}

        def _search(self, qs):
            q = qs.get("q", "").strip()
            if not q:
                return {"widgets": [], "total": 0}
            from .config import cloud_enabled
            if not cloud_enabled():
                return {"widgets": [], "total": 0}
            from .cloud import _get
            params = {"q": q}
            if qs.get("domain"): params["domain"] = qs["domain"]
            if qs.get("language"): params["language"] = qs["language"]
            query_string = urllib.parse.urlencode(params)
            try:
                result = _get(f"/v1/widgets/search?{query_string}")
                return result
            except Exception as e:
                return {"widgets": [], "error": str(e)}

        def _search_users(self, qs):
            q = qs.get("q", "").strip()
            if not q:
                return {"users": [], "total": 0}
            from .cloud import search_users
            try:
                return search_users(q)
            except Exception as e:
                return {"users": [], "error": str(e)}

        def _cloud_reviews(self, owner, widget_id):
            from .cloud import get_reviews
            try:
                return get_reviews(owner, widget_id)
            except Exception as e:
                return {"reviews": [], "error": str(e)}

        def _inspect(self, owner, widget_id):
            from .cloud import _get
            try:
                return _get(f"/v1/widgets/{owner}/{widget_id}")
            except Exception as e:
                return {"error": str(e)}

        def _versions(self, widget_id):
            import os, json as _json
            widget = None
            for w in engine.widgets:
                if w.get("id") == widget_id or w.get("name") == widget_id:
                    widget = w
                    break
            if not widget or "path" not in widget:
                return {"widget_id": widget_id, "versions": [], "changelog": []}
            history_dir = os.path.join(widget["path"], "history")
            versions = []
            if os.path.isdir(history_dir):
                versions = sorted(
                    [d for d in os.listdir(history_dir) if os.path.isdir(os.path.join(history_dir, d))],
                    reverse=True,
                )
            changelog = []
            changelog_path = os.path.join(widget["path"], "changelog.json")
            if os.path.isfile(changelog_path):
                try:
                    with open(changelog_path, encoding="utf-8") as f:
                        changelog = _json.load(f)
                except Exception:
                    pass
            return {"widget_id": widget_id, "versions": versions, "changelog": changelog}

        def _files(self, widget_id, owner="", version=""):
            import os
            # Find the widget in local library
            widget = None
            for w in engine.widgets:
                if w.get("id") == widget_id or w.get("name") == widget_id:
                    widget = w
                    break

            if widget and "path" in widget:
                base = widget["path"]
                if version:
                    version_path = os.path.join(base, "history", version)
                    if os.path.isdir(version_path):
                        base = version_path
                    else:
                        return {"widget_id": widget_id, "files": {}, "error": f"Version {version} not found"}
                return self._local_files(widget_id, base)

            # Not local - try fetching from cloud
            return self._cloud_files(widget_id, owner)

        def _local_files(self, widget_id, base):
            import os
            files = {}
            skip_dirs = {"__pycache__", ".pytest_cache", "history", ".git", "node_modules"}
            skip_files = {".coverage", ".validation_stamp.json", ".dep_cache.json"}
            max_size = 500_000

            for root, dirs, filenames in os.walk(base):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fname in filenames:
                    if fname in skip_files:
                        continue
                    fpath = os.path.join(root, fname)
                    relpath = os.path.relpath(fpath, base)
                    try:
                        size = os.path.getsize(fpath)
                        if size > max_size:
                            files[relpath] = f"[File too large: {size} bytes]"
                            continue
                        with open(fpath, "r", errors="replace", encoding="utf-8") as f:
                            files[relpath] = f.read()
                    except Exception:
                        files[relpath] = "[Could not read file]"

            return {"widget_id": widget_id, "files": files}

        def _cloud_files(self, widget_id, owner=""):
            if not owner:
                return {"widget_id": widget_id, "files": {}, "error": "No owner specified"}

            from .cloud import _get
            result = _get(f"/v1/widgets/{owner}/{widget_id}/files")
            if "error" in result:
                return {"widget_id": widget_id, "files": {}, "error": result["error"]}
            return {"widget_id": widget_id, "files": result.get("files", {})}

        def _send_json(self, data):
            body = json.dumps(data).encode()
            self._send(200, "application/json", body)

        def _send(self, code, content_type, body):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


def _write_pid(pid, port):
    os.makedirs(_state_dir(), exist_ok=True)
    with open(_pid_file(), "w", encoding="utf-8") as f:
        f.write(f"{pid}\n{port}\n")


def _is_pid_alive(pid):
    """Check if a process is still running (cross-platform)."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False


def _kill_pid(pid):
    """Terminate a process by PID (cross-platform)."""
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _read_pid():
    """Returns (pid, port) or (None, None)."""
    pf = _pid_file()
    if not os.path.exists(pf):
        return None, None
    try:
        lines = open(pf, encoding="utf-8").read().strip().splitlines()
        pid = int(lines[0])
        port = int(lines[1]) if len(lines) > 1 else 0
        if not _is_pid_alive(pid):
            os.remove(pf)
            return None, None
        return pid, port
    except (ValueError, IndexError):
        os.remove(pf)
        return None, None


def stop():
    """Stop a running dashboard. Returns True if one was stopped."""
    pid, _ = _read_pid()
    if pid is None:
        return False
    _kill_pid(pid)
    pf = _pid_file()
    if os.path.exists(pf):
        os.remove(pf)
    return True


def _serve_foreground(handler, port, pid_file):
    """Run the server in the foreground (used by spawned subprocess on Windows)."""
    server = http.server.HTTPServer(("127.0.0.1", port), handler)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, lambda *_: (server.server_close(), sys.exit(0)))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if os.path.exists(pid_file):
            os.remove(pid_file)


def serve(engine, port=9473, open_browser=True):
    # Restart if already running
    existing_pid, _ = _read_pid()
    if existing_pid is not None:
        print("  Restarting dashboard...")
        stop()

    handler = _make_handler(engine)

    if sys.platform == "win32":
        # Windows: spawn a detached subprocess running this module
        import subprocess
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        actual_port = server.server_address[1]
        server.server_close()
        url = f"http://127.0.0.1:{actual_port}"

        proc = subprocess.Popen(
            [sys.executable, "-m", "cartograph.dashboard", str(actual_port)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _write_pid(proc.pid, actual_port)
        print(f"\n  Cartograph Dashboard -> {url}  (pid {proc.pid})")
        print(f"  Run 'cartograph dashboard --stop' to stop it.\n")
        if open_browser:
            threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    else:
        # Unix: fork to background
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        actual_port = server.server_address[1]
        url = f"http://127.0.0.1:{actual_port}"

        pid = os.fork()
        if pid > 0:
            _write_pid(pid, actual_port)
            print(f"\n  Cartograph Dashboard -> {url}  (pid {pid})")
            print(f"  Run 'cartograph dashboard --stop' to stop it.\n")
            if open_browser:
                threading.Timer(0.3, lambda: webbrowser.open(url)).start()
            return

        # Child - detach and serve
        os.setsid()
        sys.stdin.close()
        devnull = open(os.devnull, "w")
        sys.stdout = devnull
        sys.stderr = devnull

        signal.signal(signal.SIGTERM, lambda *_: (server.server_close(), sys.exit(0)))

        try:
            server.serve_forever()
        finally:
            server.server_close()
            pf = _pid_file()
            if os.path.exists(pf):
                os.remove(pf)


# Allow running as subprocess for Windows background serving:
#   python -m cartograph.dashboard <port>
if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_PORT
    from .engine import Cartograph, LIBRARY_PATH
    engine = Cartograph(LIBRARY_PATH)
    handler = _make_handler(engine)
    _serve_foreground(handler, port, _pid_file())
