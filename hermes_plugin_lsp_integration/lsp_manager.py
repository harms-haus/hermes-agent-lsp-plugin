"""Core LSP server management: lifecycle, lazy install, diagnostics.

Uses raw JSON-RPC over stdin/stdout for maximum reliability across
all LSP server implementations.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Session-level caches ───────────────────────────────────────────────────
_installed_binaries: set[str] = set()


def _ensure_npm_global_on_path() -> None:
    """Add npm global bin to PATH if not already present."""
    try:
        prefix = subprocess.run(
            ["npm", "config", "get", "prefix"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        bin_dir = os.path.join(prefix, "bin")
        if os.path.isdir(bin_dir) and bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    except Exception:
        pass


def is_binary_available(binary: str) -> bool:
    """Return True if *binary* is on PATH or was installed this session."""
    if binary in _installed_binaries:
        return True
    _ensure_npm_global_on_path()
    return shutil.which(binary) is not None


# ─── Project markers for workspace root detection ────────────────────────────
PROJECT_MARKERS: tuple[str, ...] = (
    "package.json",
    "Cargo.toml",
    "pyproject.toml",
    "setup.py",
    "tsconfig.json",
    "rust-toolchain.toml",
    "Gemfile",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
    "*.csproj",
    "*.sln",
    "*.xcodeproj",
    "*.podspec",
    ".git",
    "mix.exs",
    "composer.json",
    "rebar.config",
    "*.cabal",
    ".gitignore",  # fallback — most projects have one
)


# ─── Session-level caches ───────────────────────────────────────────────────
def clear_session_cache() -> None:
    """Clear the session-level install cache.

    Called when ``force_refresh`` is requested so the next diagnostic
    run re-checks binary availability.
    """
    global _installed_binaries
    _installed_binaries = set()
    logger.info("LSP session cache cleared")


# ─── Workspace root resolution ───────────────────────────────────────────────
def resolve_workspace_root(file_path: str) -> Path:
    """Walk up from *file_path* to find a project root marker.

    Falls back to the file's parent directory.
    """
    current = Path(file_path).resolve()
    if not current.exists():
        return current.parent.resolve()

    for parent in [current] + list(current.parents):
        for marker in PROJECT_MARKERS:
            if _marker_exists(parent, marker):
                return parent
        if parent == parent.parent:
            break
    return current.parent.resolve()


def _marker_exists(directory: Path, pattern: str) -> bool:
    """Check if a marker (supports glob wildcards) exists in *directory*."""
    if "*" in pattern or "?" in pattern:
        return any(directory.glob(pattern))
    return (directory / pattern).exists()


# ─── Binary / install helpers ────────────────────────────────────────────────
def check_binary_installed(binary: str) -> bool:
    """Return True if *binary* is on PATH."""
    return shutil.which(binary) is not None


def install_server(install_command: str) -> bool:
    """Run the install command and return True on success."""
    logger.info("Installing LSP server: %s", install_command)
    try:
        result = subprocess.run(
            install_command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error(
                "Install failed (rc=%d): %s", result.returncode, result.stderr[:500]
            )
            return False
        logger.info("LSP server installed successfully")
        return True
    except subprocess.TimeoutExpired:
        logger.error("Install command timed out after 300s")
        return False
    except Exception as exc:
        logger.error("Install command failed: %s", exc)
        return False


# ─── Raw JSON-RPC helpers ────────────────────────────────────────────────────

def _lsp_request(message_id: int, method: str, params: dict) -> dict:
    """Build a JSON-RPC request dict."""
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": method,
        "params": params,
    }


def _lsp_notification(method: str, params: dict) -> dict:
    """Build a JSON-RPC notification dict."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
    }


def _send_message(proc: subprocess.Popen, msg: dict) -> None:
    """Send a JSON-RPC message to the LSP server's stdin."""
    body = json.dumps(msg)
    header = f"Content-Length: {len(body)}\r\n\r\n"
    proc.stdin.write(header.encode() + body.encode())
    proc.stdin.flush()


def _build_server_command(binary: str, lang_config: dict) -> list[str] | None:
    """Build the command to start an LSP server."""
    server_args = lang_config.get("server_args")
    if server_args is not None:
        return [binary] + list(server_args)

    # Generic fallback: binary with --stdio
    return [binary, "--stdio"]



def get_diagnostics(file_path: str, lang_config: dict) -> list:
    """Main entry point — get LSP diagnostics for a file.

    Uses a persistent LSP server session (reused across calls), opens
    the file, waits for diagnostics, then returns them. The server
    stays alive for subsequent requests.

    Parameters
    ----------
    file_path : str
        Absolute path to the file to analyse.
    lang_config : dict
        Merged language configuration from config.get_language_config().

    Returns
    -------
    list
        List of Diagnostic dicts (may be empty).
    """
    server = lang_config.get("server", "")
    binary = lang_config.get("binary", server)
    install_command = lang_config.get("install_command", "")
    language_id = lang_config.get("language_id", "")
    timeout = float(lang_config.get("timeout", 15))

    # ── Lazy install ──────────────────────────────────────────────────────
    if not is_binary_available(binary):
        if install_command:
            if not install_server(install_command):
                logger.warning(
                    "Failed to install %s; skipping diagnostics", binary
                )
                return []
            _installed_binaries.add(binary)
            if not is_binary_available(binary):
                logger.warning(
                    "Binary %s still not found after install", binary
                )
                return []
        else:
            logger.warning(
                "Binary %s not found and no install_command configured", binary
            )
            return []

    # ── Resolve workspace root ────────────────────────────────────────────
    workspace_root = resolve_workspace_root(file_path)

    # ── Get or create persistent session ─────────────────────────────────
    session = get_or_create_session(workspace_root, lang_config)
    if session is None:
        logger.warning(
            "Failed to start LSP server %s; skipping diagnostics", binary
        )
        return []

    # ── Open the document (didOpen/didChange handled by session) ──────────
    session.open_document(file_path)

    # ── Collect diagnostics via notification callback ─────────────────────
    file_uri = Path(file_path).resolve().as_uri()
    collected: list = []

    def _capture_diagnostics(params: dict) -> None:
        if params.get("uri") == file_uri:
            collected.extend(params.get("diagnostics", []))

    session.on_notification("textDocument/publishDiagnostics", _capture_diagnostics)

    # ── Wait for server to process the document ──────────────────────────
    indexing_delay = float(lang_config.get("indexing_delay", 2.0))
    time.sleep(indexing_delay)

    # ── Return collected diagnostics (server stays alive for reuse) ───────
    session.touch()
    return collected


# ─── Persistent LSP server session ──────────────────────────────────────────

# Mapping from common file extensions to LSP languageId values.
_LANG_ID_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".hs": "haskell",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".ml": "ocaml",
    ".zig": "zig",
    ".dart": "dart",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".sh": "shellscript",
    ".bash": "shellscript",
    ".zsh": "shellscript",
    ".sql": "sql",
    ".dockerfile": "dockerfile",
    ".vue": "vue",
    ".svelte": "svelte",
}


def _guess_language_id(file_path: str, lang_config: dict) -> str:
    """Determine the LSP languageId for a file path."""
    cfg_id = lang_config.get("language_id", "")
    if cfg_id:
        return cfg_id
    ext = Path(file_path).suffix
    return _LANG_ID_MAP.get(ext, "plaintext")


class ServerSession:
    """Manages a single persistent LSP server subprocess.

    Provides request/response, notification handling, document management,
    and graceful lifecycle control for a long-lived LSP server instance.
    """

    def __init__(
        self,
        binary: str,
        cmd: list[str],
        workspace_root: Path | str,
        lang_config: dict,
    ) -> None:
        self.binary = binary
        self.cmd = cmd
        self.workspace_root = Path(workspace_root)
        self.lang_config = lang_config

        self.proc: subprocess.Popen | None = None
        self.last_access: float = 0.0
        self._msg_id: int = 0
        self._lock = threading.Lock()
        self._response_events: dict[int, threading.Event] = {}
        self._responses: dict[int, dict] = {}
        self._notification_callbacks: dict[str, list] = {}
        self._reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()
        self._initialized: bool = False
        self._opened_documents: set[str] = set()
        self._doc_versions: dict[str, int] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> bool:
        """Spawn the LSP server, send initialize, and start the reader loop.

        Returns True on success, False on failure.
        """
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.workspace_root),
            )
        except Exception as exc:
            logger.error("Failed to start LSP server %s: %s", self.binary, exc)
            return False

        # Start the background reader thread BEFORE sending initialize,
        # so that the response can be received by _send_and_wait.
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True
        )
        self._reader_thread.start()

        # Send initialize request
        init_id = self._next_id()
        init_request = _lsp_request(
            init_id,
            "initialize",
            {
                "processId": None,
                "rootUri": self.workspace_root.as_uri(),
                "capabilities": {
                    "textDocument": {
                        "publishDiagnostics": {"relatedInformation": True},
                        "definition": {"linkSupport": True},
                    }
                },
            },
        )
        self._send_message(init_request)

        # Wait for initialize response (reader thread is already consuming stdout)
        response = self._send_and_wait(init_id, timeout=30.0)
        if response is None:
            logger.error("initialize request timed out or failed for %s", self.binary)
            self._teardown_proc()
            return False

        if "error" in response:
            logger.error(
                "initialize returned error for %s: %s",
                self.binary,
                response["error"],
            )
            self._teardown_proc()
            return False

        # Send initialized notification
        self._send_message(_lsp_notification("initialized", {}))

        self._initialized = True
        self.touch()
        logger.info("LSP server %s initialized successfully", self.binary)
        return True

    def stop(self) -> None:
        """Gracefully shut down the LSP server and clean up resources."""
        self._stop_reader.set()

        if self._initialized and self.proc is not None:
            try:
                # Send shutdown request
                shutdown_id = self._next_id()
                self._send_message(
                    _lsp_request(shutdown_id, "shutdown", {})
                )
                self._send_and_wait(shutdown_id, timeout=5.0)
            except Exception:
                pass

            try:
                # Send exit notification
                self._send_message(_lsp_notification("exit", {}))
            except Exception:
                pass

            try:
                self.proc.stdin.close()
            except Exception:
                pass

            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)

        self._initialized = False
        logger.info("LSP server %s stopped", self.binary)

    # ── Communication ────────────────────────────────────────────────────

    def send_request(
        self, method: str, params: dict, timeout: float = 30.0
    ) -> dict | None:
        """Send a JSON-RPC request and wait for the response.

        Returns the ``result`` field on success, or ``None`` on timeout.
        Raises ``RuntimeError`` if the server returned an error response.
        """
        with self._lock:
            msg_id = self._next_id()
            event = threading.Event()
            self._response_events[msg_id] = event
            request = _lsp_request(msg_id, method, params)
            self._send_message(request)

        if event.wait(timeout=timeout):
            with self._lock:
                response = self._responses.pop(msg_id, {})
                self._response_events.pop(msg_id, None)

            if "error" in response:
                raise RuntimeError(
                    f"LSP error for {method}: {response['error']}"
                )
            return response.get("result")

        # Timeout — clean up
        with self._lock:
            self._response_events.pop(msg_id, None)
            self._responses.pop(msg_id, None)
        logger.warning(
            "Request %s (id=%d) timed out after %.1fs", method, msg_id, timeout
        )
        return None

    def send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        self._send_message(_lsp_notification(method, params))

    def on_notification(self, method: str, callback) -> None:
        """Register a callback for a server-to-client notification."""
        self._notification_callbacks.setdefault(method, []).append(callback)

    def touch(self) -> None:
        """Update the last-access timestamp."""
        self.last_access = time.monotonic()

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        """True if the subprocess is running."""
        return self.proc is not None and self.proc.poll() is None

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last touch() call. Zero if never touched."""
        if self.last_access == 0.0:
            return 0.0
        return time.monotonic() - self.last_access

    # ── Document management ──────────────────────────────────────────────

    def open_document(self, file_path: str) -> None:
        """Open a document in the LSP server, or send didChange if already open."""
        uri = Path(file_path).resolve().as_uri()
        language_id = _guess_language_id(file_path, self.lang_config)

        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Cannot read file %s: %s", file_path, exc)
            return

        if uri not in self._opened_documents:
            # First time: send textDocument/didOpen
            self.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": content,
                    }
                },
            )
            self._opened_documents.add(uri)
            self._doc_versions[uri] = 1
        else:
            # Already open: send textDocument/didChange
            self._doc_versions.setdefault(uri, 1)
            self._doc_versions[uri] += 1
            self.send_notification(
                "textDocument/didChange",
                {
                    "textDocument": {
                        "uri": uri,
                        "version": self._doc_versions[uri],
                    },
                    "contentChanges": [{"text": content}],
                },
            )

        self.touch()

    # ── Internal helpers ─────────────────────────────────────────────────

    def _next_id(self) -> int:
        """Generate the next JSON-RPC message ID. Must be called under _lock."""
        self._msg_id += 1
        return self._msg_id

    def _send_message(self, msg: dict) -> None:
        """Serialize and send a JSON-RPC message to the server's stdin."""
        if self.proc is None or self.proc.stdin is None:
            logger.error("Cannot send message: proc is None")
            return
        body = json.dumps(msg)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        self.proc.stdin.write(header.encode() + body.encode())
        self.proc.stdin.flush()

    def _send_and_wait(
        self, msg_id: int, timeout: float = 30.0
    ) -> dict | None:
        """Internal synchronous wait helper used during startup.

        The caller must have already sent the request message. This
        places the Event in _response_events and blocks until the
        response arrives or the timeout expires.
        """
        with self._lock:
            event = threading.Event()
            self._response_events[msg_id] = event

        if event.wait(timeout=timeout):
            with self._lock:
                return self._responses.pop(msg_id, {})
        else:
            with self._lock:
                self._response_events.pop(msg_id, None)
                self._responses.pop(msg_id, None)
            return None

    def _reader_loop(self) -> None:
        """Background thread: read stdout, parse messages, dispatch."""
        buf = b""
        while not self._stop_reader.is_set():
            data = self.proc.stdout.read(1) if self.proc else None
            if not data:
                break
            buf += data

            while b"\r\n\r\n" in buf:
                idx = buf.index(b"\r\n\r\n")
                hdr = buf[:idx].decode("utf-8", errors="replace")
                try:
                    length = int(
                        hdr.split("Content-Length:")[1]
                        .split("\r")[0]
                        .strip()
                    )
                except (IndexError, ValueError):
                    buf = buf[idx + 4 :]
                    continue

                rest = buf[idx + 4 :]
                if len(rest) < length:
                    while len(rest) < length and not self._stop_reader.is_set():
                        chunk = self.proc.stdout.read(length - len(rest)) if self.proc else None
                        if not chunk:
                            break
                        rest += chunk
                    if len(rest) < length:
                        break

                try:
                    msg = json.loads(
                        rest[:length].decode("utf-8", errors="replace")
                    )
                except json.JSONDecodeError:
                    buf = rest[length:]
                    continue

                buf = rest[length:]

                # Dispatch: response vs notification
                if "id" in msg:
                    # Response to a request we sent
                    msg_id = msg["id"]
                    with self._lock:
                        self._responses[msg_id] = msg
                        event = self._response_events.get(msg_id)
                    if event is not None:
                        event.set()
                elif "method" in msg:
                    # Server-initiated notification
                    method = msg["method"]
                    callbacks = self._notification_callbacks.get(method, [])
                    for cb in callbacks:
                        try:
                            cb(msg.get("params", {}))
                        except Exception:
                            logger.exception(
                                "Error in notification callback for %s", method
                            )

    def _teardown_proc(self) -> None:
        """Clean up a partially-started process (e.g., on init failure)."""
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


# ─── ServerPool registry and idle cleanup ────────────────────────────────

# Key: (workspace_root_str, language_id) -> ServerSession
_server_pool: dict[tuple[str, str], "ServerSession"] = {}
_pool_lock = threading.Lock()
_cleanup_stop = threading.Event()
_cleanup_thread: threading.Thread | None = None


def get_or_create_session(
    workspace_root: Path, lang_config: dict
) -> "ServerSession | None":
    """Get an existing session or create a new one for the workspace/language.

    Parameters
    ----------
    workspace_root : Path
        The workspace root directory.
    lang_config : dict
        Merged language configuration from config.get_language_config().

    Returns
    -------
    ServerSession | None
        A running ServerSession, or None if the server failed to start.
    """
    key = (str(workspace_root), lang_config["language_id"])
    binary = lang_config.get("binary", lang_config.get("server", ""))

    with _pool_lock:
        session = _server_pool.get(key)
        if session is not None and session.is_alive:
            session.touch()
            return session

    # Not alive or not found — create a new one
    cmd = _build_server_command(binary, lang_config)
    if cmd is None:
        return None

    session = ServerSession(binary, cmd, workspace_root, lang_config)
    if session.start():
        with _pool_lock:
            existing = _server_pool.get(key)
            if existing is not None and existing.is_alive:
                session.stop()
                existing.touch()
                return existing
            _server_pool[key] = session
        return session

    return None


def cleanup_idle_sessions(max_idle: float = 600.0) -> None:
    """Stop and remove sessions that have been idle too long or died.

    Parameters
    ----------
    max_idle : float
        Maximum idle time in seconds before a session is considered stale.
    """
    with _pool_lock:
        stale_keys = [
            key
            for key, session in _server_pool.items()
            if session.idle_seconds > max_idle or not session.is_alive
        ]
        for key in stale_keys:
            session = _server_pool.pop(key)
            try:
                session.stop()
            except Exception:
                logger.exception("Error stopping idle session %s", key)


def _idle_cleanup_loop(
    check_interval: float = 60.0, max_idle: float = 600.0
) -> None:
    """Daemon thread target: periodically clean up idle sessions."""
    while not _cleanup_stop.is_set():
        _cleanup_stop.wait(timeout=check_interval)
        if not _cleanup_stop.is_set():
            cleanup_idle_sessions(max_idle)


def start_idle_cleanup_daemon(
    check_interval: float = 60.0, max_idle: float = 600.0
) -> None:
    """Start the idle cleanup daemon thread.

    Parameters
    ----------
    check_interval : float
        How often (in seconds) to check for idle sessions.
    max_idle : float
        Maximum idle time in seconds before a session is reclaimed.
    """
    global _cleanup_thread
    if _cleanup_thread is not None and _cleanup_thread.is_alive():
        return
    _cleanup_stop.clear()
    _cleanup_thread = threading.Thread(
        target=_idle_cleanup_loop,
        args=(check_interval, max_idle),
        daemon=True,
    )
    _cleanup_thread.start()
    logger.info(
        "Idle cleanup daemon started (interval=%.1fs, max_idle=%.1fs)",
        check_interval,
        max_idle,
    )


def cleanup_all_sessions() -> None:
    """Stop all sessions and clear the pool."""
    _cleanup_stop.set()
    if _cleanup_thread is not None:
        _cleanup_thread.join(timeout=5.0)
    with _pool_lock:
        for key, session in list(_server_pool.items()):
            try:
                session.stop()
            except Exception:
                logger.exception("Error stopping session %s", key)
        _server_pool.clear()
    logger.info("All LSP sessions cleaned up")


def close_document(file_path: str) -> None:
    """Remove a document from all sessions' opened-document sets.

    This allows the session to re-open the document with fresh content
    if needed.

    Parameters
    ----------
    file_path : str
        Absolute path to the file to remove from tracked documents.
    """
    uri = Path(file_path).resolve().as_uri()
    with _pool_lock:
        for session in _server_pool.values():
            if uri in session._opened_documents:
                session.send_notification(
                    "textDocument/didClose",
                    {"textDocument": {"uri": uri}},
                )
                session._doc_versions.pop(uri, None)
                session._opened_documents.discard(uri)


# ─── Location parsing helper ────────────────────────────────────────────────

def _parse_location(loc: dict) -> dict:
    """Parse a Location or LocationLink dict into a human-readable form.

    Handles both:
    - Location: {uri, range: {start: {line, character}, end: {line, character}}}
    - LocationLink: {targetUri, targetRange, targetSelectionRange}

    Returns a dict with keys: file, line, column, range.
    Line numbers are 1-indexed; columns are 0-indexed.
    """
    from urllib.parse import unquote

    if "targetUri" in loc:
        # LocationLink
        uri = loc["targetUri"]
        sel_range = loc.get("targetSelectionRange", loc.get("targetRange", {}))
        full_range = loc.get("targetRange", {})
    else:
        # Location
        uri = loc.get("uri", "")
        sel_range = loc.get("range", {})
        full_range = loc.get("range", {})

    # Convert file:// URI to local path
    if uri.startswith("file://"):
        file_path = unquote(uri[7:])
        # On Windows, strip leading slash from /C:/...
        if file_path.startswith("/") and len(file_path) > 2 and file_path[2] == ":":
            file_path = file_path[1:]
    else:
        file_path = uri

    start_line = sel_range.get("start", {}).get("line", 0)
    start_col = sel_range.get("start", {}).get("character", 0)
    end_line = full_range.get("end", {}).get("line", start_line)
    end_col = full_range.get("end", {}).get("character", start_col)

    return {
        "file": file_path,
        "line": start_line + 1,  # Convert to 1-indexed
        "column": start_col,
        "range": {
            "start_line": start_line + 1,
            "start_column": start_col,
            "end_line": end_line + 1,
            "end_column": end_col,
        },
    }


# ─── Navigation functions ───────────────────────────────────────────────────

def goto_definition(
    file_path: str,
    line: int,
    character: int,
    lang_config: dict,
) -> dict:
    """Find the definition at the given position.

    Parameters
    ----------
    file_path : str
        Absolute path to the file.
    line : int
        1-indexed line number.
    character : int
        0-indexed character position.
    lang_config : dict
        Merged language configuration from config.get_language_config().

    Returns
    -------
    dict
        {\"success\": True, \"locations\": [...]} on success,
        {\"success\": False, \"error\": \"...\"} on failure.
    """
    # Resolve workspace root
    workspace_root = resolve_workspace_root(file_path)

    # Get or create persistent session
    session = get_or_create_session(workspace_root, lang_config)
    if session is None:
        return {
            "success": False,
            "error": "Failed to start LSP server",
        }

    # Open the document
    session.open_document(file_path)

    # Build file URI
    file_uri = Path(file_path).resolve().as_uri()

    # Get timeout from config (default 30s)
    timeout = float(lang_config.get("timeout", 30))

    # Send textDocument/definition request
    try:
        result = session.send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line - 1, "character": character},
            },
            timeout=timeout,
        )
    except RuntimeError as exc:
        return {
            "success": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Unexpected error: {exc}",
        }

    # Parse response: could be Location, Location[], LocationLink[], or null
    if result is None:
        session.touch()
        return {
            "success": True,
            "locations": [],
            "message": "No definition found",
        }

    # Normalize to a list
    if isinstance(result, dict):
        result = [result]
    elif not isinstance(result, list):
        session.touch()
        return {
            "success": True,
            "locations": [],
            "message": "No definition found",
        }

    locations = []
    for loc in result:
        try:
            locations.append(_parse_location(loc))
        except Exception as exc:
            logger.warning("Failed to parse definition location: %s", exc)

    session.touch()
    return {
        "success": True,
        "locations": locations,
    }


def find_references(
    file_path: str,
    line: int,
    character: int,
    include_declaration: bool,
    lang_config: dict,
) -> dict:
    """Find all references to the symbol at the given position.

    Parameters
    ----------
    file_path : str
        Absolute path to the file.
    line : int
        1-indexed line number.
    character : int
        0-indexed character position.
    include_declaration : bool
        Whether to include the declaration in the results.
    lang_config : dict
        Merged language configuration from config.get_language_config().

    Returns
    -------
    dict
        {\"success\": True, \"references\": [...], \"count\": N} on success,
        {\"success\": False, \"error\": \"...\"} on failure.
    """
    # Resolve workspace root
    workspace_root = resolve_workspace_root(file_path)

    # Get or create persistent session
    session = get_or_create_session(workspace_root, lang_config)
    if session is None:
        return {
            "success": False,
            "error": "Failed to start LSP server",
        }

    # Open the document
    session.open_document(file_path)

    # Build file URI
    file_uri = Path(file_path).resolve().as_uri()

    # Get timeout from config (default 30s)
    timeout = float(lang_config.get("timeout", 30))

    # Send textDocument/references request
    try:
        result = session.send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": file_uri},
                "position": {"line": line - 1, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
            timeout=timeout,
        )
    except RuntimeError as exc:
        return {
            "success": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Unexpected error: {exc}",
        }

    # Response is Location[] or null
    if result is None:
        session.touch()
        return {
            "success": True,
            "references": [],
            "count": 0,
            "message": "No references found",
        }

    if not isinstance(result, list):
        result = [result]

    references = []
    for loc in result:
        try:
            references.append(_parse_location(loc))
        except Exception as exc:
            logger.warning("Failed to parse reference location: %s", exc)

    session.touch()
    return {
        "success": True,
        "references": references,
        "count": len(references),
    }
