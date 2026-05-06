# Add Three New LSP Tools to lsp-integration Plugin

## Request

Add three new LSP tools to complement `lsp_goto_definition` and `lsp_find_references`:

1. **`lsp_find_symbol`** — LSP `workspace/symbol` request
   - Searches workspace-wide for symbols matching a query string
   - Returns symbol name, kind (function, class, variable, etc.), location

2. **`lsp_rename_symbol`** — LSP `textDocument/rename` request
   - Renames a symbol at a given position across the workspace
   - Returns a workspace edit describing all changes (file URIs + ranges + new text)

3. **`lsp_call_hierarchy`** — LSP `textDocument/prepareCallHierarchy` + `callHierarchy/incomingCalls` + `callHierarchy/outgoingCalls`
   - Prepares call hierarchy items at a position
   - Then fetches incoming and/or outgoing call relationships
   - Returns callers and callees with their locations

## Context

- Plugin directory: `./hermes_plugin_lsp_integration/` (symlinked to hermes plugins folder)
- Existing tools to follow as patterns: `lsp_goto_definition`, `lsp_find_references`
- `lsp_manager.py` has `ServerSession`, `get_or_create_session()`, `_parse_location()`, etc.
- `__init__.py` has handler functions and `register()` with `ctx.register_tool()`
- Handler pattern: validate args -> resolve language -> get session -> send LSP request -> parse response -> return JSON
