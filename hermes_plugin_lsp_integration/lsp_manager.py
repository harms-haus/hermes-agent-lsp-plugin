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


def _read_messages_threaded(
    proc: subprocess.Popen,
    timeout: float,
) -> list[dict]:
    """Read all available LSP messages from stdout within timeout.

    Uses a background thread with blocking reads (select is unreliable
    with subprocess pipes on some platforms).

    Returns list of parsed JSON-RPC message dicts.
    """
    msg_queue: queue.Queue[dict] = queue.Queue()
    stop_event = threading.Event()

    def _reader():
        buf = b""
        while not stop_event.is_set():
            data = proc.stdout.read(1)
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
                    while len(rest) < length:
                        chunk = proc.stdout.read(length - len(rest))
                        if not chunk:
                            break
                        rest += chunk
                    if len(rest) < length:
                        break

                try:
                    msg = json.loads(rest[:length].decode("utf-8", errors="replace"))
                    msg_queue.put(msg)
                except json.JSONDecodeError:
                    pass
                buf = rest[length:]

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    deadline = time.monotonic() + timeout
    messages: list[dict] = []

    while time.monotonic() < deadline:
        try:
            msg = msg_queue.get(timeout=0.2)
            messages.append(msg)
        except queue.Empty:
            if messages and time.monotonic() > deadline - 2:
                break

    stop_event.set()
    return messages


def _build_server_command(binary: str, lang_config: dict) -> list[str] | None:
    """Build the command to start an LSP server."""
    server_args = lang_config.get("server_args")
    if server_args is not None:
        return [binary] + list(server_args)

    # Generic fallback: binary with --stdio
    return [binary, "--stdio"]


def _run_lsp_session(
    proc: subprocess.Popen,
    workspace_root: Path,
    file_uri: str,
    language_id: str,
    content: str,
    timeout: float,
) -> list:
    """Run a complete LSP session: init -> open -> collect diagnostics."""
    msg_id = [0]

    def next_id() -> int:
        msg_id[0] += 1
        return msg_id[0]

    # 1. Initialize
    _send_message(proc, _lsp_request(next_id(), "initialize", {
        "processId": None,
        "rootUri": workspace_root.as_uri(),
        "capabilities": {
            "textDocument": {
                "publishDiagnostics": {"relatedInformation": True}
            }
        }
    }))

    # Wait for initialize response
    _read_messages_threaded(proc, timeout=5.0)

    # 2. Send initialized notification
    _send_message(proc, _lsp_notification("initialized", {}))

    # 3. Open the document
    _send_message(proc, _lsp_notification("textDocument/didOpen", {
        "textDocument": {
            "uri": file_uri,
            "languageId": language_id,
            "version": 1,
            "text": content,
        }
    }))

    # 4. Collect all messages, looking for diagnostics
    all_messages = _read_messages_threaded(proc, timeout=timeout)

    # 5. Extract diagnostics
    diagnostics: list = []
    for msg in all_messages:
        if msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            if params.get("uri") == file_uri:
                diagnostics.extend(params.get("diagnostics", []))

    return diagnostics


def get_diagnostics(file_path: str, lang_config: dict) -> list:
    """Main entry point — get LSP diagnostics for a file.

    Spawns a fresh LSP server process, opens the file, waits for
    diagnostics, then shuts down. This is simple and reliable.

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

    # ── Build server command ────────────────────────────────────────────────
    cmd = _build_server_command(binary, lang_config)
    if cmd is None:
        return []

    # ── Read file content ────────────────────────────────────────────────
    try:
        content = Path(file_path).read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Cannot read file %s: %s", file_path, exc)
        return []

    file_uri = Path(file_path).resolve().as_uri()

    # ── Start LSP server ─────────────────────────────────────────────────
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workspace_root),
        )
    except Exception as exc:
        logger.warning("Failed to start LSP server %s: %s", binary, exc)
        return []

    try:
        diagnostics = _run_lsp_session(proc, workspace_root, file_uri, language_id, content, timeout)
    finally:
        # Best-effort shutdown
        try:
            _send_message(proc, _lsp_request(99, "shutdown", {}))
            _send_message(proc, _lsp_notification("exit", {}))
            proc.stdin.close()
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass

    return diagnostics
