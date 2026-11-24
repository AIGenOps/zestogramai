import os
import time
import json
import secrets
import shutil
import logging
import threading
import mimetypes
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from src.config import get_data_dir
from src.downloader import _download_sync, DownloadError

logger = logging.getLogger(__name__)

# Cache index.html content in memory
HTML_PATH = os.path.join(os.path.dirname(__file__), "web", "index.html")
_CACHED_HTML = None

def get_index_html() -> bytes:
    global _CACHED_HTML
    if _CACHED_HTML is None:
        try:
            with open(HTML_PATH, "rb") as f:
                _CACHED_HTML = f.read()
        except Exception as e:
            logger.error(f"Failed to read index.html: {e}")
            _CACHED_HTML = b"<!DOCTYPE html><html><body><h1>Zestogram Downloader</h1></body></html>"
    return _CACHED_HTML

# Thread-safe in-memory registry of active web downloads
_DOWNLOADS_LOCK = threading.Lock()
_ACTIVE_DOWNLOADS = {}  # token -> { path, filename, title, size, mime, dir, created_at }

def _format_size(num_bytes: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"

def cleanup_expired_downloads():
    now = time.time()
    expired_tokens = []
    with _DOWNLOADS_LOCK:
        for token, info in list(_ACTIVE_DOWNLOADS.items()):
            # Expire after 15 minutes
            if now - info.get("created_at", 0) > 900:
                expired_tokens.append(token)
                target_dir = info.get("dir")
                if target_dir and os.path.exists(target_dir):
                    shutil.rmtree(target_dir, ignore_errors=True)
                _ACTIVE_DOWNLOADS.pop(token, None)
    if expired_tokens:
        logger.info(f"Cleaned up {len(expired_tokens)} expired web download sessions.")

def background_cleanup_worker():
    while True:
        try:
            time.sleep(300)
            cleanup_expired_downloads()
        except Exception as e:
            logger.error(f"Error in web download cleanup worker: {e}")

class ZestogramWebHandler(BaseHTTPRequestHandler):
    server_version = "ZestogramWeb/1.0"

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_minimal_ok(self, send_body=True):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self._send_cors_headers()
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _send_json(self, status_code: int, data: dict):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        parsed = urlparse(self.path)
        if parsed.path in ["/", "/health", "/ping"]:
            self._send_minimal_ok(send_body=False)
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Dedicated health check paths
        if path in ["/health", "/ping"]:
            self._send_minimal_ok(send_body=True)
            return

        # 2. Root path handling
        if path == "/":
            user_agent = self.headers.get("User-Agent", "")
            accept = self.headers.get("Accept", "")

            # Keepalive pingers, Cloudflare worker, curl, or non-HTML requests receive minimal 2-byte OK
            if (
                "CloudflareWorker" in user_agent
                or user_agent.startswith("curl/")
                or ("text/html" not in accept and "*/*" in accept and "Mozilla" not in user_agent)
            ):
                self._send_minimal_ok(send_body=True)
                return

            # Web browser visit: serve index.html
            html_bytes = get_index_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        # 3. File download endpoint: /api/file/<token>
        if path.startswith("/api/file/"):
            token = path.split("/api/file/")[-1].strip("/")
            file_info = None
            with _DOWNLOADS_LOCK:
                file_info = _ACTIVE_DOWNLOADS.get(token)

            if not file_info or not os.path.exists(file_info.get("path", "")):
                self._send_json(404, {"success": False, "error": "File expired or not found."})
                return

            file_path = file_info["path"]
            filename = file_info["filename"]
            file_size = file_info["size"]
            mime_type = file_info.get("mime", "application/octet-stream")

            try:
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(file_size))
                # RFC 5987 / URL-safe filename header
                safe_filename = filename.replace('"', '')
                self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
                self._send_cors_headers()
                self.end_headers()

                with open(file_path, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                logger.error(f"Error streaming file {filename}: {e}")
            return

        # Default 404
        self._send_json(404, {"success": False, "error": "Endpoint not found."})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path != "/api/download":
            self._send_json(404, {"success": False, "error": "Endpoint not found."})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0 or content_length > 65536:
            self._send_json(400, {"success": False, "error": "Invalid request payload size."})
            return

        try:
            raw_body = self.rfile.read(content_length)
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"success": False, "error": "Malformed JSON payload."})
            return

        target_url = (body.get("url") or "").strip()
        format_type = (body.get("format") or "video").strip().lower()
        audio_only = (format_type == "audio")

        if not target_url or not (target_url.startswith("http://") or target_url.startswith("https://")):
            self._send_json(400, {"success": False, "error": "Please provide a valid HTTP or HTTPS media URL."})
            return

        # Generate unique temporary session directory
        token = secrets.token_hex(12)
        web_base_dir = os.path.join(get_data_dir(), "web_downloads")
        session_dir = os.path.join(web_base_dir, token)
        os.makedirs(session_dir, exist_ok=True)

        try:
            # Download using existing engine
            downloaded = _download_sync(target_url, session_dir, audio_only=audio_only)
            if not downloaded:
                shutil.rmtree(session_dir, ignore_errors=True)
                self._send_json(400, {"success": False, "error": "No media could be extracted from this URL."})
                return

            primary_item = downloaded[0]
            file_path = primary_item["path"]
            filename = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)
            caption = primary_item.get("caption") or filename

            # Determine MIME type
            mime_type, _ = mimetypes.guess_type(file_path)
            if not mime_type:
                mime_type = "audio/mpeg" if audio_only else "video/mp4"

            # Register download session
            with _DOWNLOADS_LOCK:
                _ACTIVE_DOWNLOADS[token] = {
                    "path": file_path,
                    "filename": filename,
                    "title": caption[:120] if caption else filename,
                    "size": file_size,
                    "mime": mime_type,
                    "dir": session_dir,
                    "created_at": time.time()
                }

            self._send_json(200, {
                "success": True,
                "token": token,
                "title": caption[:120] if caption else filename,
                "filename": filename,
                "size_bytes": file_size,
                "size_human": _format_size(file_size),
                "format": "mp3" if audio_only else "mp4",
                "download_url": f"/api/file/{token}"
            })

        except DownloadError as de:
            shutil.rmtree(session_dir, ignore_errors=True)
            logger.warning(f"Web download user error for {target_url}: {de.message}")
            self._send_json(400, {"success": False, "error": de.message})
        except Exception as e:
            shutil.rmtree(session_dir, ignore_errors=True)
            logger.error(f"Web download unexpected error for {target_url}: {e}", exc_info=True)
            self._send_json(500, {"success": False, "error": f"Download failed: {str(e)}"})

    def log_message(self, format, *args):
        # Silence default standard error logging to keep container logs clean
        pass

def start_web_server(port: int):
    # Launch background cleanup thread
    cleanup_thread = threading.Thread(target=background_cleanup_worker, daemon=True)
    cleanup_thread.start()

    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), ZestogramWebHandler)
        server.daemon_threads = True
        logger.info(f"Zestogram Multi-threaded Web and Health Server running on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"Could not start Web server on port {port}: {e}")
