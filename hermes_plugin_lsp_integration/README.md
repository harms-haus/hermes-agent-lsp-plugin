# LSP Integration Plugin

> 🔍 **30-language LSP diagnostics** — automatically after every file edit,
> or on-demand via the `lsp_diagnostics` tool.
> 🎯 **Navigation** — `lsp_goto_definition` and `lsp_find_references` tools.

| | |
|:---|:---|
| **Version** | 1.2.0 |
| **License** | MIT |
| **Hermes** | ≥ 0.11.0 |

---

## What It Does

1. **Automatic diagnostics** — every time `patch` or `write_file` modifies a
   source file, the plugin spins up the correct LSP server, runs diagnostics,
   and appends the results to the tool output. The model sees errors and
   warnings immediately and can fix them in the next turn.

2. **On-demand diagnostics** — call `lsp_diagnostics("/path/to/file.py")`
   whenever you want a fresh diagnostic report. Use `force_refresh=true` to
   restart the LSP server first (useful after large refactors or when the
   server state feels stale).

3. **Navigation tools** — `lsp_goto_definition` and `lsp_find_references` let
   you jump to symbol definitions and find all references across the workspace.
   These use **file+position** input (not symbol name), requiring a running
   LSP server. See [Navigation Tools](#navigation-tools) below.

4. **Persistent server management** — LSP servers are kept alive across
   multiple tool calls for performance. An idle cleanup daemon automatically
   reclaims servers that have been idle for 600 seconds (configurable via
   `idle_timeout`). All sessions are cleanly shut down when Hermes exits.

---

## Supported Languages (30)

| Language | Server | Binary | Install hint |
|:---|:---|:---|:---|
| Python | pyright | `pyright-langserver` | `pip install pyright` |
| JavaScript / TypeScript | typescript-language-server | `typescript-language-server` | `npm install -g typescript-language-server typescript` |
| Rust | rust-analyzer | `rust-analyzer` | `rustup component add rust-analyzer` |
| C / C++ | clangd | `clangd` | Bundled with clang/llvm |
| Go | gopls | `gopls` | `go install golang.org/x/tools/gopls@latest` |
| Java | jdtls | `jdtls` | Eclipse jdtls releases |
| Kotlin | kotlin-language-server | `kotlin-language-server` | fwcd/kotlin-language-server releases |
| Scala | metals | `metals` | `cs install metals` |
| C# | omnisharp | `omnisharp` | OmniSharp releases |
| F# | fsautocomplete | `fsautocomplete` | `dotnet tool install -g fsautocomplete` |
| HTML | vscode-html-languageserver | `html-languageserver` | `npm install -g vscode-html-languageserver-bin` |
| CSS | vscode-css-languageserver | `css-languageserver` | `npm install -g vscode-css-languageserver-bin` |
| Haskell | haskell-language-server | `haskell-language-server-wrapper` | `ghcup install hls` |
| Elixir | elixir-ls | `elixir-ls` | elixir-lsp releases |
| Erlang | erlang-ls | `erlang_ls` | erlang-ls releases |
| Ruby | solargraph | `solargraph` | `gem install solargraph` |
| PHP | intelephense | `intelephense` | `npm install -g intelephense` |
| Lua | lua-language-server | `lua-language-server` | LuaLS releases |
| Bash | bash-language-server | `bash-language-server` | `npm install -g bash-language-server` |
| JSON | vscode-json-languageserver | `vscode-json-languageserver` | `npm install -g vscode-json-languageserver` |
| YAML | yaml-language-server | `yaml-language-server` | `npm install -g yaml-language-server` |
| TOML | taplo | `taplo` | `cargo install taplo-cli --features lsp` |
| Dockerfile | dockerfile-language-server | `dockerfile-language-server-nodejs` | `npm install -g dockerfile-language-server-nodejs` |
| SQL | sqls | `sqls` | `go install github.com/lighttiger2505/sqls@latest` |
| Zig | zls | `zls` | zigtools/zls releases |
| R | languageserver (R) | `R` | `R -e 'install.packages("languageserver")'` |
| PowerShell | PowerShellEditorServices | `pwsh` | PowerShell/PowerShellEditorServices |
| Perl | PLS | `perl` | `cpanm PLS` |
| Swift | sourcekit-lsp | `sourcekit-lsp` | Bundled with Swift toolchain |
| Markdown | marksman | `marksman` | artempyanykh/marksman releases |

---

## Enabling the Plugin

Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - lsp-integration
```

Then restart Hermes (`/reset` in a chat session, or exit and relaunch).

---

## Configuration

All settings live under the `lsp_integration` key in `~/.hermes/config.yaml`:

```yaml
lsp_integration:
  enabled: true          # master toggle
  timeout: 15            # seconds to wait for diagnostics per file
  languages:             # override or extend any language below
    python:
      timeout: 20        # per-language override
    mylang:
      extensions: [".mylang"]
      server: "my-lsp"
      binary: "my-lsp"
      server_args: ["--stdio"]
      language_id: "mylang"
```

### Per-language keys

| Key | Description | Example |
|:---|:---|:---|
| `extensions` | File extensions that map to this language | `[".py"]` |
| `server` | Human-readable server name | `pyright` |
| `binary` | Executable name on PATH | `pyright-langserver` |
| `install_command` | Shell command to auto-install | `pip install pyright` |
| `check_command` | Verifies binary is present | `["pyright-langserver", "--version"]` |
| `server_args` | CLI args when spawning the server | `["--stdio"]` |
| `language_id` | LSP language identifier | `python` |

> **Note:** `install_command` supports a leading `#` comment when the server
> cannot be auto-installed (e.g. `clangd`). The plugin skips auto-install for
> commented commands and logs a warning.

---

## Using the `lsp_diagnostics` Tool

### Syntax

```
lsp_diagnostics(filename="/path/to/file.py")
lsp_diagnostics(filename="/path/to/file.ts", force_refresh=true)
```

### Parameters

| Parameter | Type | Required | Description |
|:---|:---|:---|:---|
| `filename` | `string` | **Yes** | Absolute path to the source file |
| `force_refresh` | `boolean` | No | Restart the LSP server before collecting diagnostics. Default `false`. |

### When to use `force_refresh`

- After large cross-file refactors (the server may have stale ASTs)
- When switching between major dependency versions
- When the diagnostics feel out of sync with the actual file content
- When a language server is known to cache aggressively (e.g. TypeScript)

---

## Navigation Tools

> **Note:** Navigation tools require the LSP server to be running. Servers are
> started automatically on first use and kept alive for subsequent calls.
> Idle servers are reclaimed after 600 seconds by default.

### Workflow: File+Position vs Symbol Name

Unlike some IDEs where you type a symbol name to search, the LSP navigation
tools use **file position** input: you provide a `filename`, `line`, and
`character` pointing to the symbol's location in the source file. The LSP
server resolves the symbol at that position and returns definitions or
references.

### `lsp_goto_definition`

Jump to the definition of the symbol at the given position.

#### Syntax

```
lsp_goto_definition(filename="/path/to/file.py", line=42, character=10)
lsp_goto_definition(filename="/path/to/file.py", line=42, character=10, force_refresh=true)
```

#### Parameters

| Parameter | Type | Required | Description |
|:---|:---|:---|:---|
| `filename` | `string` | **Yes** | Absolute path to the file containing the symbol |
| `line` | `integer` | **Yes** | 1-indexed line number of the symbol |
| `character` | `integer` | **Yes** | 0-indexed character position of the symbol |
| `force_refresh` | `boolean` | No | Restart the LSP server before querying. Default `false`. |

#### Returns

JSON with `success`, and either `locations` (list of definition locations) or
`error`.

### `lsp_find_references`

Find all references to the symbol at the given position across the workspace.

#### Syntax

```
lsp_find_references(filename="/path/to/file.py", line=42, character=10)
lsp_find_references(filename="/path/to/file.py", line=42, character=10, include_declaration=false)
lsp_find_references(filename="/path/to/file.py", line=42, character=10, force_refresh=true)
```

#### Parameters

| Parameter | Type | Required | Description |
|:---|:---|:---|:---|
| `filename` | `string` | **Yes** | Absolute path to the file containing the symbol |
| `line` | `integer` | **Yes** | 1-indexed line number of the symbol |
| `character` | `integer` | **Yes** | 0-indexed character position of the symbol |
| `include_declaration` | `boolean` | No | Include the declaration in results. Default `true`. |
| `force_refresh` | `boolean` | No | Restart the LSP server before querying. Default `false`. |

#### Returns

JSON with `success`, and either `references` (list of reference locations) and
`count`, or `error`.

---

## Persistent Server Management

Navigation tools (`lsp_goto_definition`, `lsp_find_references`) require a
persistent LSP server session. Unlike diagnostics (which start and stop the
server per call), navigation tools keep servers alive across multiple calls
for better performance.

### Idle Cleanup

- **Idle timeout**: 600 seconds (10 minutes) by default, configurable via
  `idle_timeout` in `~/.hermes/config.yaml`.
- **Cleanup interval**: Every 60 seconds, a background daemon thread checks
  for idle or dead sessions and reclaims them.
- **Graceful shutdown**: An `atexit` handler ensures all persistent LSP
  sessions are cleanly shut down when Hermes exits.

### Configuration

```yaml
lsp_integration:
  enabled: true
  idle_timeout: 600       # seconds before idle server is killed
  cleanup_interval: 60    # seconds between cleanup checks
  timeout: 15             # seconds to wait for diagnostics per file
```

> **Note:** The idle cleanup daemon starts automatically when the plugin
> loads. You do not need to manually start or stop it.

---

## How It Works (Architecture)

```
┌─────────────────┐     ┌─────────────┐     ┌────────────────────┐
│  patch /        │────▶│  LSP Plugin │────▶│  Spawn LSP server  │
│  write_file     │     │  (hook)     │     │  (lazy install)    │
└─────────────────┘     └─────────────┘     └────────────────────┘
         │                                            │
         │     ┌──────────────────────────────────────┘
         │     ▼
         │  ┌─────────────┐
         └──│ JSON-RPC    │
            │ over stdio  │
            └─────────────┘
                   │
                   ▼
            ┌─────────────────────┐
            │ Diagnostics +       │
            │ Server stays alive  │
            └─────────────────────┘
                   │
                   ▼
            ┌─────────────────────┐
            │ Server reused for   │
            │ subsequent requests │
            │ (idle cleanup: 600s)│
            └─────────────────────┘
```

1. **Hook fires** on every `patch`/`write_file` result.
2. **Extension → language** mapping resolves the correct LSP server config.
3. **Lazy install** runs `install_command` if the binary is missing.
4. **Workspace root** is detected by walking up for project markers
   (`package.json`, `Cargo.toml`, `.git`, ...).
5. **Persistent server** spawns the server on first use and keeps it alive.
   Sends `initialize`, `textDocument/didOpen`, collects
   `textDocument/publishDiagnostics`. The server is reused for subsequent
   requests instead of being torn down.
6. **Idle cleanup** reclaims servers after 600 seconds of inactivity or on
   plugin unload.
7. **Markdown table** is appended to the tool result for the model to read.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|:---|:---|:---|
| "No diagnostics found" | Server binary not on PATH | Install the server (see table above) |
| Timeout / no output | Slow server or large workspace | Increase `timeout` in config |
| Wrong workspace root | Missing project marker | Add a `.git` or config file to the repo root |
| Stale diagnostics | Server caching | Use `force_refresh=true` |
| Hook not firing | Plugin not enabled | Check `plugins.enabled` in config.yaml |

---

## Contributing

PRs welcome! When adding a new language:

1. Add the config block to `config.py` under `DEFAULT_CONFIG["languages"]`.
2. Include `extensions`, `server`, `binary`, `install_command`,
   `check_command`, `server_args`, and `language_id`.
3. Update the README language table.
4. If the server does not support `--stdio`, set the correct `server_args`.

---

*Built with ❤️ for the Hermes Agent ecosystem.*
