"""LSP Integration plugin — entry point.

Registers a ``transform_tool_result`` hook that appends LSP diagnostics
to the result of ``patch`` and ``write_file`` operations, plus a new
``lsp_diagnostics`` tool that allows on-demand diagnostic runs.
"""

from __future__ import annotations

import atexit
import json
import logging
from pathlib import Path
from typing import Any, Optional

from . import config, diagnostics, lsp_manager

logger = logging.getLogger(__name__)


def _get_diagnostics_for_file(path: str) -> str:
    """Internal helper to fetch and format diagnostics for a given file.

    Returns a formatted markdown string, or an empty string if no
    diagnostics are found / applicable.
    """
    if not path:
        return ""

    ext = Path(path).suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        # Also try exact filename match (e.g. Dockerfile)
        filename = Path(path).name
        for lang_name, lang_cfg in config._get_merged_config().get("languages", {}).items():
            if not isinstance(lang_cfg, dict):
                continue
            if filename in [e.lstrip(".") for e in lang_cfg.get("extensions", [])]:
                language = lang_name
                break

    if not language:
        return ""

    lang_config = config.get_language_config(language)
    if not lang_config:
        return ""

    try:
        diag_result = lsp_manager.get_diagnostics(path, lang_config)
        if not diag_result:
            return ""
        return diagnostics.format_diagnostics(diag_result, path)
    except Exception as exc:
        logger.warning("LSP diagnostics failed for %s: %s", path, exc)
        return ""


def _on_transform_tool_result(
    tool_name: str,
    args: dict[str, Any],
    result: str,
    **kwargs: Any,
) -> Optional[str]:
    """Append LSP diagnostics to patch/write_file results.

    Returns the modified result string, or None to leave it unchanged.
    ALL exceptions are caught to never break file edits.
    """
    # Only handle file-editing tools
    if tool_name not in ("patch", "write_file"):
        return None

    # Check master toggle
    if not config.is_enabled():
        return None

    try:
        path = args.get("path", "")
        if not path:
            return None

        diag_text = _get_diagnostics_for_file(path)
        if diag_text:
            return result.rstrip() + "\n\n" + diag_text

        return None

    except Exception as exc:
        # Never propagate — log and return None (original result unchanged)
        logger.warning("LSP diagnostics hook failed: %s", exc, exc_info=True)
        return None


def _lsp_diagnostics_handler(**kwargs) -> str:
    """Handler for the ``lsp_diagnostics`` tool.

    Parameters
    ----------
    filename : str
        Absolute path to the file to analyse.
    force_refresh : bool, optional
        If True, restart the LSP server before collecting diagnostics.
        Default is False.
    """
    filename = kwargs.get("filename", "")
    force_refresh = kwargs.get("force_refresh", False)

    if not filename:
        return json.dumps({
            "success": False,
            "error": "No filename provided.",
        })

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({
            "success": False,
            "error": f"File not found: {filename}",
        })

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({
            "success": False,
            "error": "LSP integration is disabled in config.",
        })

    # Force refresh: clear any cached server state (best-effort)
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for %s", filename)

    # Get diagnostics
    try:
        diag_text = _get_diagnostics_for_file(filename)
        if diag_text:
            return json.dumps({
                "success": True,
                "file": filename,
                "diagnostics": diag_text,
            })
        else:
            return json.dumps({
                "success": True,
                "file": filename,
                "diagnostics": "No diagnostics found (file is clean or language server unavailable).",
            })
    except Exception as exc:
        logger.warning("lsp_diagnostics tool failed: %s", exc, exc_info=True)
        return json.dumps({
            "success": False,
            "error": str(exc),
        })


def _lsp_goto_definition_handler(**kwargs) -> str:
    """Handler for the ``lsp_goto_definition`` tool.

    Parameters
    ----------
    filename : str
        Absolute path to the file.
    line : int
        1-indexed line number.
    character : int
        0-indexed character position.
    force_refresh : bool, optional
        If True, restart the LSP server before querying. Default is False.
    """
    filename = kwargs.get("filename", "")
    line = kwargs.get("line")
    character = kwargs.get("character")
    force_refresh = kwargs.get("force_refresh", False)

    if not filename:
        return json.dumps({"success": False, "error": "No filename provided."})

    if line is None or character is None:
        return json.dumps({
            "success": False,
            "error": "Both 'line' and 'character' parameters are required.",
        })

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({"success": False, "error": f"File not found: {filename}"})

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({"success": False, "error": "LSP integration is disabled in config."})

    # Resolve language from extension
    ext = file_path.suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        return json.dumps({
            "success": False,
            "error": f"No language mapping for extension '{ext}'.",
        })

    lang_config = config.get_language_config(language)
    if not lang_config:
        return json.dumps({
            "success": False,
            "error": f"No LSP configuration for language '{language}'.",
        })

    # Force refresh if requested
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for goto_definition on %s", filename)

    try:
        result = lsp_manager.goto_definition(filename, line, character, lang_config)
        return json.dumps(result)
    except Exception as exc:
        logger.warning("lsp_goto_definition tool failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


def _lsp_find_references_handler(**kwargs) -> str:
    """Handler for the ``lsp_find_references`` tool.

    Parameters
    ----------
    filename : str
        Absolute path to the file.
    line : int
        1-indexed line number.
    character : int
        0-indexed character position.
    include_declaration : bool, optional
        Whether to include the declaration in results. Default is True.
    force_refresh : bool, optional
        If True, restart the LSP server before querying. Default is False.
    """
    filename = kwargs.get("filename", "")
    line = kwargs.get("line")
    character = kwargs.get("character")
    include_declaration = kwargs.get("include_declaration", True)
    force_refresh = kwargs.get("force_refresh", False)

    if not filename:
        return json.dumps({"success": False, "error": "No filename provided."})

    if line is None or character is None:
        return json.dumps({
            "success": False,
            "error": "Both 'line' and 'character' parameters are required.",
        })

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({"success": False, "error": f"File not found: {filename}"})

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({"success": False, "error": "LSP integration is disabled in config."})

    # Resolve language from extension
    ext = file_path.suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        return json.dumps({
            "success": False,
            "error": f"No language mapping for extension '{ext}'.",
        })

    lang_config = config.get_language_config(language)
    if not lang_config:
        return json.dumps({
            "success": False,
            "error": f"No LSP configuration for language '{language}'.",
        })

    # Force refresh if requested
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for find_references on %s", filename)

    try:
        result = lsp_manager.find_references(
            filename, line, character, include_declaration, lang_config,
        )
        return json.dumps(result)
    except Exception as exc:
        logger.warning("lsp_find_references tool failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


def _lsp_find_symbol_handler(**kwargs) -> str:
    """Handler for the ``lsp_find_symbol`` tool.

    Parameters
    ----------
    query : str
        The search query string. Servers typically perform fuzzy matching.
    filename : str
        Absolute path to any file in the workspace (used to determine the
        workspace root and language server).
    force_refresh : bool, optional
        If True, restart the LSP server before querying. Default is False.
    """
    query = kwargs.get("query", "")
    filename = kwargs.get("filename", "")
    force_refresh = kwargs.get("force_refresh", False)

    if not query:
        return json.dumps({"success": False, "error": "No query provided."})

    if not filename:
        return json.dumps({"success": False, "error": "No filename provided."})

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({"success": False, "error": f"File not found: {filename}"})

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({"success": False, "error": "LSP integration is disabled in config."})

    # Resolve language from extension
    ext = file_path.suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        return json.dumps({
            "success": False,
            "error": f"No language mapping for extension '{ext}'.",
        })

    lang_config = config.get_language_config(language)
    if not lang_config:
        return json.dumps({
            "success": False,
            "error": f"No LSP configuration for language '{language}'.",
        })

    # Force refresh if requested
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for find_symbol on %s", filename)

    try:
        result = lsp_manager.workspace_symbol(filename, query, lang_config)
        return json.dumps(result)
    except Exception as exc:
        logger.warning("lsp_find_symbol tool failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


def _lsp_rename_symbol_handler(**kwargs) -> str:
    """Handler for the ``lsp_rename_symbol`` tool.

    Parameters
    ----------
    filename : str
        Absolute path to the file containing the symbol.
    line : int
        1-indexed line number of the symbol.
    character : int
        0-indexed character position of the symbol.
    new_name : str
        The new name for the symbol.
    force_refresh : bool, optional
        If True, restart the LSP server before querying. Default is False.
    """
    filename = kwargs.get("filename", "")
    line = kwargs.get("line")
    character = kwargs.get("character")
    new_name = kwargs.get("new_name", "")
    force_refresh = kwargs.get("force_refresh", False)

    if not filename:
        return json.dumps({"success": False, "error": "No filename provided."})

    if line is None or character is None:
        return json.dumps({
            "success": False,
            "error": "Both 'line' and 'character' parameters are required.",
        })

    if not new_name:
        return json.dumps({"success": False, "error": "No new_name provided."})

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({"success": False, "error": f"File not found: {filename}"})

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({"success": False, "error": "LSP integration is disabled in config."})

    # Resolve language from extension
    ext = file_path.suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        return json.dumps({
            "success": False,
            "error": f"No language mapping for extension '{ext}'.",
        })

    lang_config = config.get_language_config(language)
    if not lang_config:
        return json.dumps({
            "success": False,
            "error": f"No LSP configuration for language '{language}'.",
        })

    # Force refresh if requested
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for rename_symbol on %s", filename)

    try:
        result = lsp_manager.rename_symbol(filename, line, character, new_name, lang_config)
        return json.dumps(result)
    except Exception as exc:
        logger.warning("lsp_rename_symbol tool failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


def _lsp_call_hierarchy_handler(**kwargs) -> str:
    """Handler for the ``lsp_call_hierarchy`` tool.

    Parameters
    ----------
    filename : str
        Absolute path to the file containing the function/method.
    line : int
        1-indexed line number of the function/method.
    character : int
        0-indexed character position of the function/method name.
    direction : str, optional
        Which direction to explore: 'incoming', 'outgoing', or 'both'.
        Default is 'both'.
    force_refresh : bool, optional
        If True, restart the LSP server before querying. Default is False.
    """
    filename = kwargs.get("filename", "")
    line = kwargs.get("line")
    character = kwargs.get("character")
    direction = kwargs.get("direction", "both")
    force_refresh = kwargs.get("force_refresh", False)

    if not filename:
        return json.dumps({"success": False, "error": "No filename provided."})

    if line is None or character is None:
        return json.dumps({
            "success": False,
            "error": "Both 'line' and 'character' parameters are required.",
        })

    # Validate direction
    if direction not in ("incoming", "outgoing", "both"):
        return json.dumps({
            "success": False,
            "error": f"Invalid direction '{direction}'. Must be 'incoming', 'outgoing', or 'both'.",
        })

    # Validate file exists
    file_path = Path(filename)
    if not file_path.exists():
        return json.dumps({"success": False, "error": f"File not found: {filename}"})

    # Check if enabled
    if not config.is_enabled():
        return json.dumps({"success": False, "error": "LSP integration is disabled in config."})

    # Resolve language from extension
    ext = file_path.suffix.lower()
    extensions = config.get_extensions()
    language = extensions.get(ext)
    if not language:
        return json.dumps({
            "success": False,
            "error": f"No language mapping for extension '{ext}'.",
        })

    lang_config = config.get_language_config(language)
    if not lang_config:
        return json.dumps({
            "success": False,
            "error": f"No LSP configuration for language '{language}'.",
        })

    # Force refresh if requested
    if force_refresh:
        lsp_manager.clear_session_cache()
        logger.info("LSP force refresh requested for call_hierarchy on %s", filename)

    try:
        result = lsp_manager.call_hierarchy(filename, line, character, direction, lang_config)
        return json.dumps(result)
    except Exception as exc:
        logger.warning("lsp_call_hierarchy tool failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})


def register(ctx: Any) -> None:
    """Entry point called by the Hermes plugin loader."""
    # Warm up config cache
    config.reload_config()

    # Register the transform_tool_result hook (auto-diagnostics after edits)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    logger.info("lsp-integration plugin registered (transform_tool_result hook)")

    # Register the on-demand lsp_diagnostics tool
    ctx.register_tool(
        name="lsp_diagnostics",
        toolset="lsp",
        schema={
            "name": "lsp_diagnostics",
            "description": (
                "Run the Language Server Protocol (LSP) diagnostics for a given file. "
                "Returns a markdown-formatted diagnostic report (errors, warnings, info) "
                "produced by the appropriate language server. Use ``force_refresh=true`` "
                "to ensure the server is restarted before checking."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Absolute path to the file to analyse.",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": (
                            "If true, restart the LSP server before collecting diagnostics. "
                            "Useful when the file has changed significantly or the server state is stale."
                        ),
                        "default": False,
                    },
                },
                "required": ["filename"],
            },
        },
        handler=lambda args, **kw: _lsp_diagnostics_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Run LSP diagnostics for a file (with optional force refresh).",
        emoji="🔍",
    )
    logger.info("lsp-integration plugin registered tool: lsp_diagnostics")

    # ─── lsp_goto_definition tool ──────────────────────────────────────
    ctx.register_tool(
        name="lsp_goto_definition",
        toolset="lsp",
        schema={
            "name": "lsp_goto_definition",
            "description": (
                "Find the definition of the symbol at the given file position using the "
                "Language Server Protocol. Returns the location(s) where the symbol is "
                "defined. Use ``force_refresh=true`` to restart the LSP server before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Absolute path to the file containing the symbol.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character position of the symbol.",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": (
                            "If true, restart the LSP server before querying. "
                            "Useful when the server state is stale."
                        ),
                        "default": False,
                    },
                },
                "required": ["filename", "line", "character"],
            },
        },
        handler=lambda args, **kw: _lsp_goto_definition_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Find the definition of a symbol at a given file position (LSP go-to-definition).",
        emoji="🎯",
    )
    logger.info("lsp-integration plugin registered tool: lsp_goto_definition")

    # ─── lsp_find_references tool ──────────────────────────────────────
    ctx.register_tool(
        name="lsp_find_references",
        toolset="lsp",
        schema={
            "name": "lsp_find_references",
            "description": (
                "Find all references to the symbol at the given file position using the "
                "Language Server Protocol. Returns all locations where the symbol is used "
                "across the workspace. Use ``force_refresh=true`` to restart the LSP server "
                "before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Absolute path to the file containing the symbol.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character position of the symbol.",
                    },
                    "include_declaration": {
                        "type": "boolean",
                        "description": (
                            "Whether to include the declaration in the results. "
                            "Default is true."
                        ),
                        "default": True,
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": (
                            "If true, restart the LSP server before querying. "
                            "Useful when the server state is stale."
                        ),
                        "default": False,
                    },
                },
                "required": ["filename", "line", "character"],
            },
        },
        handler=lambda args, **kw: _lsp_find_references_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Find all references to a symbol at a given file position (LSP find-references).",
        emoji="🔗",
    )
    logger.info("lsp-integration plugin registered tool: lsp_find_references")

    # ─── lsp_find_symbol tool ──────────────────────────────────────────
    ctx.register_tool(
        name="lsp_find_symbol",
        toolset="lsp",
        schema={
            "name": "lsp_find_symbol",
            "description": (
                "Search for symbols across the workspace using the Language Server Protocol. "
                "Returns matching symbols with their names, kinds (function, class, variable, etc.), "
                "and locations. Use ``force_refresh=true`` to restart the LSP server before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string. Servers typically perform fuzzy matching.",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Absolute path to any file in the workspace "
                            "(used to determine the workspace root and language server)."
                        ),
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "If true, restart the LSP server before querying.",
                        "default": False,
                    },
                },
                "required": ["query", "filename"],
            },
        },
        handler=lambda args, **kw: _lsp_find_symbol_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Search for symbols across the workspace (LSP workspace/symbol).",
        emoji="📋",
    )
    logger.info("lsp-integration plugin registered tool: lsp_find_symbol")

    # ─── lsp_rename_symbol tool ────────────────────────────────────────
    ctx.register_tool(
        name="lsp_rename_symbol",
        toolset="lsp",
        schema={
            "name": "lsp_rename_symbol",
            "description": (
                "Rename a symbol at the given file position across the workspace using the "
                "Language Server Protocol. Returns a workspace edit describing all files and "
                "ranges that would change. The edits are NOT applied automatically — use the "
                "``patch`` tool to apply them. Use ``force_refresh=true`` to restart the LSP "
                "server before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Absolute path to the file containing the symbol.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character position of the symbol.",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "The new name for the symbol.",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "If true, restart the LSP server before querying.",
                        "default": False,
                    },
                },
                "required": ["filename", "line", "character", "new_name"],
            },
        },
        handler=lambda args, **kw: _lsp_rename_symbol_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Rename a symbol across the workspace (LSP textDocument/rename).",
        emoji="✏️",
    )
    logger.info("lsp-integration plugin registered tool: lsp_rename_symbol")

    # ─── lsp_call_hierarchy tool ───────────────────────────────────────
    ctx.register_tool(
        name="lsp_call_hierarchy",
        toolset="lsp",
        schema={
            "name": "lsp_call_hierarchy",
            "description": (
                "Explore the call hierarchy of a function or method at the given position "
                "using the Language Server Protocol. Returns incoming callers (who calls this) "
                "and/or outgoing callees (what this calls). Use ``force_refresh=true`` to "
                "restart the LSP server before querying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Absolute path to the file containing the function/method.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-indexed line number of the function/method.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character position of the function/method name.",
                    },
                    "direction": {
                        "type": "string",
                        "description": (
                            "Which direction to explore: 'incoming' (who calls this), "
                            "'outgoing' (what this calls), or 'both'. Default is 'both'."
                        ),
                        "enum": ["incoming", "outgoing", "both"],
                        "default": "both",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "If true, restart the LSP server before querying.",
                        "default": False,
                    },
                },
                "required": ["filename", "line", "character"],
            },
        },
        handler=lambda args, **kw: _lsp_call_hierarchy_handler(**args),
        check_fn=lambda: True,
        requires_env=[],
        description="Explore the call hierarchy of a function/method (LSP callHierarchy).",
        emoji="🌳",
    )
    logger.info("lsp-integration plugin registered tool: lsp_call_hierarchy")

    # ─── Persistent server management ──────────────────────────────────
    # Start idle cleanup daemon (reclaims servers idle >600s)
    lsp_manager.start_idle_cleanup_daemon(
        check_interval=config.get_cleanup_interval(),
        max_idle=config.get_idle_timeout(),
    )
    logger.info(
        "lsp-integration: idle cleanup daemon started (interval=%ds, max_idle=%ds)",
        config.get_cleanup_interval(),
        config.get_idle_timeout(),
    )

    # Register atexit handler to clean up all persistent LSP sessions
    atexit.register(lsp_manager.cleanup_all_sessions)
    logger.info("lsp-integration: atexit handler registered for session cleanup")
