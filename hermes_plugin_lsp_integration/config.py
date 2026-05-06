"""Configuration loading for the LSP integration plugin.

Loads settings from ``~/.hermes/config.yaml`` under the ``lsp_integration`` key
and merges them with sensible defaults.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict = {
    "enabled": True,
    "timeout": 15,
    "idle_timeout": 600,       # seconds before idle server is killed
    "cleanup_interval": 60,    # seconds between cleanup checks
    "request_timeout": 30,     # default timeout for LSP requests
    "indexing_delay": 2.0,     # seconds to wait after init for indexing
    "languages": {
        # ── Scripting / dynamic ─────────────────────────────────────────────
        "python": {
            "extensions": [".py"],
            "server": "pyright",
            "binary": "pyright-langserver",
            "install_command": "pip install pyright",
            "check_command": ["pyright-langserver", "--version"],
            "server_args": ["--stdio"],
            "language_id": "python",
            "indexing_delay": 5.0,
        },
        # ── JavaScript ecosystem ───────────────────────────────────────────
        "javascript": {
            "extensions": [".js", ".jsx", ".mjs", ".cjs"],
            "server": "typescript-language-server",
            "binary": "typescript-language-server",
            "install_command": "npm install -g typescript-language-server typescript",
            "check_command": ["typescript-language-server", "--version"],
            "server_args": ["--stdio"],
            "language_id": "javascript",
        },
        "typescript": {
            "extensions": [".ts", ".tsx", ".mts", ".cts"],
            "server": "typescript-language-server",
            "binary": "typescript-language-server",
            "install_command": "npm install -g typescript-language-server typescript",
            "check_command": ["typescript-language-server", "--version"],
            "server_args": ["--stdio"],
            "language_id": "typescript",
            "indexing_delay": 5.0,
        },
        # ── Systems / compiled ─────────────────────────────────────────────
        "rust": {
            "extensions": [".rs"],
            "server": "rust-analyzer",
            "binary": "rust-analyzer",
            "install_command": "rustup component add rust-analyzer",
            "check_command": ["rust-analyzer", "--version"],
            "server_args": [],
            "language_id": "rust",
            "indexing_delay": 8.0,
        },
        "c": {
            "extensions": [".c", ".h"],
            "server": "clangd",
            "binary": "clangd",
            "install_command": "",
            "check_command": ["clangd", "--version"],
            "server_args": ["--stdio"],
            "language_id": "c",
        },
        "cpp": {
            "extensions": [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h"],
            "server": "clangd",
            "binary": "clangd",
            "install_command": "",
            "check_command": ["clangd", "--version"],
            "server_args": ["--stdio"],
            "language_id": "cpp",
        },
        "go": {
            "extensions": [".go"],
            "server": "gopls",
            "binary": "gopls",
            "install_command": "go install golang.org/x/tools/gopls@latest",
            "check_command": ["gopls", "version"],
            "server_args": ["serve"],
            "language_id": "go",
            "indexing_delay": 5.0,
        },
        # ── JVM ────────────────────────────────────────────────────────────
        "java": {
            "extensions": [".java"],
            "server": "jdtls",
            "binary": "jdtls",
            "install_command": "",
            "check_command": ["jdtls", "--version"],
            "server_args": [],
            "language_id": "java",
        },
        "kotlin": {
            "extensions": [".kt", ".kts"],
            "server": "kotlin-language-server",
            "binary": "kotlin-language-server",
            "install_command": "",
            "check_command": ["kotlin-language-server", "--version"],
            "server_args": [],
            "language_id": "kotlin",
        },
        "scala": {
            "extensions": [".scala", ".sc"],
            "server": "metals",
            "binary": "metals",
            "install_command": "cs install metals",
            "check_command": ["metals", "--version"],
            "server_args": [],
            "language_id": "scala",
        },
        # ── .NET / Microsoft ───────────────────────────────────────────────
        "csharp": {
            "extensions": [".cs"],
            "server": "omnisharp",
            "binary": "omnisharp",
            "install_command": "",
            "check_command": ["omnisharp", "--version"],
            "server_args": ["--stdio"],
            "language_id": "csharp",
        },
        "fsharp": {
            "extensions": [".fs", ".fsx"],
            "server": "fsautocomplete",
            "binary": "fsautocomplete",
            "install_command": "dotnet tool install -g fsautocomplete",
            "check_command": ["fsautocomplete", "--version"],
            "server_args": ["--stdio"],
            "language_id": "fsharp",
        },
        # ── Web / markup ──────────────────────────────────────────────────
        "html": {
            "extensions": [".html", ".htm"],
            "server": "html-languageserver",
            "binary": "html-languageserver",
            "install_command": "npm install -g vscode-html-languageserver-bin",
            "check_command": ["html-languageserver", "--version"],
            "server_args": ["--stdio"],
            "language_id": "html",
        },
        "css": {
            "extensions": [".css", ".scss", ".sass", ".less"],
            "server": "css-languageserver",
            "binary": "css-languageserver",
            "install_command": "npm install -g vscode-css-languageserver-bin",
            "check_command": ["css-languageserver", "--version"],
            "server_args": ["--stdio"],
            "language_id": "css",
        },
        # ── Functional ────────────────────────────────────────────────────
        "haskell": {
            "extensions": [".hs", ".lhs"],
            "server": "haskell-language-server",
            "binary": "haskell-language-server-wrapper",
            "install_command": "ghcup install hls",
            "check_command": ["haskell-language-server-wrapper", "--version"],
            "server_args": ["--lsp"],
            "language_id": "haskell",
        },
        # ── BEAM / Erlang VM ──────────────────────────────────────────────
        "elixir": {
            "extensions": [".ex", ".exs"],
            "server": "elixir-ls",
            "binary": "elixir-ls",
            "install_command": "",
            "check_command": ["elixir-ls", "--version"],
            "server_args": [],
            "language_id": "elixir",
        },
        "erlang": {
            "extensions": [".erl", ".hrl"],
            "server": "erlang-ls",
            "binary": "erlang_ls",
            "install_command": "",
            "check_command": ["erlang_ls", "--version"],
            "server_args": [],
            "language_id": "erlang",
        },
        # ── Ruby ───────────────────────────────────────────────────────────
        "ruby": {
            "extensions": [".rb", ".erb", ".rake"],
            "server": "solargraph",
            "binary": "solargraph",
            "install_command": "gem install solargraph",
            "check_command": ["solargraph", "--version"],
            "server_args": ["stdio"],
            "language_id": "ruby",
        },
        # ── PHP ────────────────────────────────────────────────────────────
        "php": {
            "extensions": [".php"],
            "server": "intelephense",
            "binary": "intelephense",
            "install_command": "npm install -g intelephense",
            "check_command": ["intelephense", "--version"],
            "server_args": ["--stdio"],
            "language_id": "php",
        },
        # ── Lua ────────────────────────────────────────────────────────────
        "lua": {
            "extensions": [".lua"],
            "server": "lua-language-server",
            "binary": "lua-language-server",
            "install_command": "",
            "check_command": ["lua-language-server", "--version"],
            "server_args": [],
            "language_id": "lua",
        },
        # ── Shell ─────────────────────────────────────────────────────────
        "bash": {
            "extensions": [".sh", ".bash", ".zsh", ".bashrc"],
            "server": "bash-language-server",
            "binary": "bash-language-server",
            "install_command": "npm install -g bash-language-server",
            "check_command": ["bash-language-server", "--version"],
            "server_args": ["start"],
            "language_id": "bash",
        },
        # ── Data / config formats ─────────────────────────────────────────
        "json": {
            "extensions": [".json"],
            "server": "vscode-json-languageserver",
            "binary": "vscode-json-languageserver",
            "install_command": "npm install -g vscode-json-languageserver",
            "check_command": ["vscode-json-languageserver", "--version"],
            "server_args": ["--stdio"],
            "language_id": "json",
        },
        "yaml": {
            "extensions": [".yaml", ".yml"],
            "server": "yaml-language-server",
            "binary": "yaml-language-server",
            "install_command": "npm install -g yaml-language-server",
            "check_command": ["yaml-language-server", "--version"],
            "server_args": ["--stdio"],
            "language_id": "yaml",
        },
        "toml": {
            "extensions": [".toml"],
            "server": "taplo",
            "binary": "taplo",
            "install_command": "cargo install taplo-cli --features lsp",
            "check_command": ["taplo", "--version"],
            "server_args": ["lsp", "stdio"],
            "language_id": "toml",
        },
        "dockerfile": {
            "extensions": [".dockerfile", "Dockerfile"],
            "server": "dockerfile-language-server",
            "binary": "dockerfile-language-server-nodejs",
            "install_command": "npm install -g dockerfile-language-server-nodejs",
            "check_command": ["dockerfile-language-server-nodejs", "--version"],
            "server_args": ["--stdio"],
            "language_id": "dockerfile",
        },
        # ── SQL ────────────────────────────────────────────────────────────
        "sql": {
            "extensions": [".sql"],
            "server": "sqls",
            "binary": "sqls",
            "install_command": "go install github.com/lighttiger2505/sqls@latest",
            "check_command": ["sqls", "--version"],
            "server_args": [],
            "language_id": "sql",
        },
        # ── Zig ────────────────────────────────────────────────────────────
        "zig": {
            "extensions": [".zig"],
            "server": "zls",
            "binary": "zls",
            "install_command": "",
            "check_command": ["zls", "--version"],
            "server_args": [],
            "language_id": "zig",
        },
        # ── R ──────────────────────────────────────────────────────────────
        "r": {
            "extensions": [".r", ".rmd"],
            "server": "r-languageserver",
            "binary": "R",
            "install_command": "R -e 'install.packages(\"languageserver\")'",
            "check_command": ["R", "-e", "library(languageserver)"],
            "server_args": ["--slave", "-e", "languageserver::run()"],
            "language_id": "r",
        },
        # ── PowerShell ─────────────────────────────────────────────────────
        "powershell": {
            "extensions": [".ps1", ".psm1", ".psd1"],
            "server": "powershell-editor-services",
            "binary": "pwsh",
            "install_command": "",
            "check_command": ["pwsh", "-Command", "Get-Module PowerShellEditorServices -ListAvailable"],
            "server_args": [],
            "language_id": "powershell",
        },
        # ── Perl ───────────────────────────────────────────────────────────
        "perl": {
            "extensions": [".pl", ".pm"],
            "server": "perl-language-server",
            "binary": "perl",
            "install_command": "cpanm PLS",
            "check_command": ["perl", "-MPLS", "-e1"],
            "server_args": ["-MPLS", "-e", "PLS->new->run"],
            "language_id": "perl",
        },
        # ── Swift ─────────────────────────────────────────────────────────
        "swift": {
            "extensions": [".swift"],
            "server": "sourcekit-lsp",
            "binary": "sourcekit-lsp",
            "install_command": "",
            "check_command": ["sourcekit-lsp", "--version"],
            "server_args": [],
            "language_id": "swift",
        },
        # ── Markdown ──────────────────────────────────────────────────────
        "markdown": {
            "extensions": [".md", ".markdown"],
            "server": "marksman",
            "binary": "marksman",
            "install_command": "",
            "check_command": ["marksman", "--version"],
            "server_args": ["server"],
            "language_id": "markdown",
        },
    },
}


def _get_hermes_home() -> Path:
    """Return the Hermes home directory."""
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home()
    except ImportError:
        return Path.home() / ".hermes"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict.

    Non-dict values in *override* completely replace the base value.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Read config.yaml and merge lsp_integration section with defaults."""
    config_path = _get_hermes_home() / "config.yaml"
    user_config: dict = {}

    if config_path.exists():
        try:
            import yaml

            with open(config_path, "r") as f:
                data = yaml.safe_load(f) or {}
            user_config = data.get("lsp_integration", {})
            if not isinstance(user_config, dict):
                logger.warning(
                    "lsp_integration in config.yaml is not a dict; using defaults"
                )
                user_config = {}
        except Exception as exc:
            logger.warning("Failed to load config.yaml: %s", exc)

    return _deep_merge(DEFAULT_CONFIG, user_config)


# Module-level cache so we don't re-read config.yaml on every call.
_cached_config: dict | None = None


def _get_merged_config() -> dict:
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def reload_config() -> None:
    """Force re-reading config.yaml (useful for testing)."""
    global _cached_config
    _cached_config = load_config()


def is_enabled() -> bool:
    """Check the master enable toggle."""
    return bool(_get_merged_config().get("enabled", True))


def get_language_config(lang: str) -> dict | None:
    """Return the merged configuration dict for a language, or None."""
    languages = _get_merged_config().get("languages", {})
    lang_cfg = languages.get(lang)
    if lang_cfg is None:
        return None
    if not isinstance(lang_cfg, dict):
        return None
    # Ensure the language key itself is present for downstream code
    lang_cfg.setdefault("server", lang)
    return lang_cfg


def get_extensions() -> dict[str, str]:
    """Build a mapping from file extension to language identifier.

    Example: {".py": "python", ".ts": "typescript", ...}
    """
    mapping: dict[str, str] = {}
    languages = _get_merged_config().get("languages", {})
    for lang_name, lang_cfg in languages.items():
        if not isinstance(lang_cfg, dict):
            continue
        for ext in lang_cfg.get("extensions", []):
            mapping[ext] = lang_name
    return mapping


def get_timeout() -> int:
    """Return the diagnostic collection timeout in seconds."""
    timeout = _get_merged_config().get("timeout", 15)
    return max(1, int(timeout))


def get_idle_timeout() -> int:
    """Return the idle timeout in seconds before an idle server is killed."""
    return int(_get_merged_config().get("idle_timeout", 600))


def get_cleanup_interval() -> int:
    """Return the interval in seconds between cleanup checks."""
    return int(_get_merged_config().get("cleanup_interval", 60))


def get_request_timeout() -> int:
    """Return the default timeout in seconds for LSP requests."""
    return int(_get_merged_config().get("request_timeout", 30))


def get_indexing_delay(lang: str) -> float:
    """Return the indexing delay in seconds for a language.

    Checks for a per-language override first, falling back to the global
    default indexing_delay.
    """
    lang_cfg = get_language_config(lang)
    if lang_cfg is not None and "indexing_delay" in lang_cfg:
        return float(lang_cfg["indexing_delay"])
    return float(_get_merged_config().get("indexing_delay", 2.0))
