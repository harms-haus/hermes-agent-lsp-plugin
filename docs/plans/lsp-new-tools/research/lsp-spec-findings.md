# LSP 3.17 Specification Findings for New Tools

Source: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/

---

## 1. workspace/symbol

### Request

- **Method:** `workspace/symbol`
- **Params:** `WorkspaceSymbolParams`

```typescript
interface WorkspaceSymbolParams extends WorkDoneProgressParams, PartialResultParams {
    /**
     * A query string to filter symbols by. Clients may send an empty
     * string here to request all symbols.
     */
    query: string;                          // REQUIRED
}
```

Note: `WorkDoneProgressParams` adds optional `workDoneToken` (progress token).
`PartialResultParams` adds optional `partialResultToken`. Neither is needed for
our basic implementation.

### Response

- **Result:** `SymbolInformation[] | WorkspaceSymbol[] | null`

**SymbolInformation** (legacy, but still widely used):

```typescript
interface SymbolInformation {
    name: string;               // REQUIRED - symbol name
    kind: SymbolKind;           // REQUIRED - numeric enum (1-26)
    tags?: SymbolTag[];         // optional (since 3.16)
    deprecated?: boolean;       // optional, deprecated in favor of tags
    location: Location;         // REQUIRED - { uri, range }
    containerName?: string;     // optional - parent symbol name
}
```

**WorkspaceSymbol** (since 3.17, preferred):

```typescript
interface WorkspaceSymbol {
    name: string;               // REQUIRED
    kind: SymbolKind;           // REQUIRED
    tags?: SymbolTag[];
    containerName?: string;
    location: Location | { uri: DocumentUri };  // REQUIRED
                                // uri-only if client supports resolveSupport
    data?: LSPAny;              // preserved for resolve request
}
```

**SymbolKind** values (subset most relevant):
File=1, Module=2, Namespace=3, Package=4, Class=5, Method=6, Property=7,
Field=8, Constructor=9, Enum=10, Interface=11, Function=12, Variable=13,
Constant=14, String=15, Number=16, Boolean=17, Array=18, Object=19,
Key=20, Null=21, EnumMember=22, Struct=23, Event=24, Operator=25,
TypeParameter=26

**Location** = `{ uri: string, range: { start: Position, end: Position } }`

### Capabilities

**Client capability** (in `initialize` params `capabilities.workspace.symbol`):

```typescript
interface WorkspaceSymbolClientCapabilities {
    dynamicRegistration?: boolean;
    symbolKind?: { valueSet?: SymbolKind[] };
    tagSupport?: { valueSet: SymbolTag[] };
    resolveSupport?: { properties: string[] };  // since 3.17
}
```

**Server capability** (in `initialize` response):
- Property path: `workspaceSymbolProvider`
- Type: `boolean | WorkspaceSymbolOptions`

For our purposes, we don't need to declare client capabilities for
`workspace/symbol` -- the server provides it if it supports it. The client
capability is purely about feature negotiation and optional features like
tag support. The server will respond regardless.

### Edge Cases

- **null response:** No symbols match -> return null. Treat as empty list.
- **Empty query string:** Legal -- means "return all symbols".
- **Mixed response type:** Result can be `SymbolInformation[]` OR
  `WorkspaceSymbol[]` -- must handle both. Check for `location.uri` vs
  `location.range` to distinguish.
- **Large results:** Server may send partial results via
  `$/partialResult` notifications if `partialResultToken` was provided.
  We should NOT use partial results -- just wait for the final response.

### Implementation Notes

- No `textDocument` parameter -- this is workspace-wide, not per-file.
- Needs a workspace root (already resolved by our `resolve_workspace_root`).
- Session must be initialized and workspace indexed before meaningful results.
- Query is a simple string -- servers typically do fuzzy/fuzzy-prefix matching.

---

## 2. textDocument/rename

### Request

- **Method:** `textDocument/rename`
- **Params:** `RenameParams`

```typescript
interface RenameParams extends TextDocumentPositionParams, WorkDoneProgressParams {
    /**
     * The new name of the symbol. If the given name is not valid the
     * request must return a ResponseError with an appropriate message set.
     */
    newName: string;                        // REQUIRED
}
```

`TextDocumentPositionParams` provides:
```typescript
{
    textDocument: TextDocumentIdentifier;   // { uri: string }  REQUIRED
    position: Position;                     // { line, character }  REQUIRED
}
```

Position is 0-indexed (line and character).

### Response

- **Result:** `WorkspaceEdit | null`

`null` should be treated the same as a `WorkspaceEdit` with no changes
(i.e., nothing to rename / symbol doesn't support renaming).

**WorkspaceEdit:**

```typescript
interface WorkspaceEdit {
    /**
     * Holds changes to existing resources.
     * Key is DocumentUri, value is array of TextEdit objects.
     */
    changes?: { [uri: DocumentUri]: TextEdit[] };

    /**
     * More advanced: documentChanges can include TextDocumentEdit,
     * CreateFile, RenameFile, DeleteFile operations.
     * Only used if client declares documentChanges capability.
     */
    documentChanges?: (
        TextDocumentEdit[] |
        (TextDocumentEdit | CreateFile | RenameFile | DeleteFile)[]
    );

    changeAnnotations?: { [id: string]: ChangeAnnotation };  // since 3.16
}
```

**TextEdit:**

```typescript
interface TextEdit {
    range: Range;       // { start: Position, end: Position }
    newText: string;    // The replacement text
}
```

### Capabilities

**Client capability** (in `initialize` params `capabilities.textDocument.rename`):

```typescript
interface RenameClientCapabilities {
    dynamicRegistration?: boolean;
    prepareSupport?: boolean;                        // since 3.12
    prepareSupportDefaultBehavior?: PrepareSupportDefaultBehavior;  // since 3.16
    honorsChangeAnnotations?: boolean;               // since 3.16
}
```

**Server capability** (in `initialize` response):
- Property path: `renameProvider`
- Type: `boolean | RenameOptions`

```typescript
interface RenameOptions extends WorkDoneProgressOptions {
    prepareProvider?: boolean;  // supports textDocument/prepareRename
}
```

**IMPORTANT:** `prepareSupport` client capability lets the server know if the
client supports `textDocument/prepareRename` (a pre-flight check). We don't
need to implement `prepareRename` -- it's optional.

### Edge Cases

- **null response:** Symbol cannot be renamed at this position (e.g., cursor on
  whitespace, keyword, or unsupported symbol). Treat as "no changes needed".
- **Error response:** Server returns error code + message when rename is
  invalid (e.g., newName is invalid, symbol can't be renamed, code doesn't
  compile). Our handler should surface this error.
- **Empty WorkspaceEdit.changes:** `{}` or `{ changes: {} }` -- symbol found
  but no rename needed (e.g., old name == new name).
- **Multiple files:** `changes` map can have entries for many URIs -- each URI
  maps to an array of TextEdits. Must iterate all entries.
- **Overlapping edits:** Edits within a single file should not overlap. Apply
  them in order (typically bottom-to-top to preserve offsets).
- **`documentChanges` vs `changes`:** If server uses `documentChanges` and we
  only support `changes`, we still need to handle `documentChanges`. Most
  servers will use `changes` for basic rename. We should handle both.

### Implementation Notes

- Requires the document to be open (`textDocument/didOpen` first).
- Position is 0-indexed line/character.
- `newName` is a plain string (the desired new identifier name).
- The WorkspaceEdit describes what to change -- the CLIENT applies the edits.
  Our tool should return the edit description, not apply it directly.
- Consider returning structured JSON with file paths, ranges, and replacement
  text so the agent can decide whether to apply via `patch` tool.

---

## 3. textDocument/prepareCallHierarchy + callHierarchy/incomingCalls / callHierarchy/outgoingCalls

### 3a. textDocument/prepareCallHierarchy

**Method:** `textDocument/prepareCallHierarchy` (since 3.16)

**Params:** `CallHierarchyPrepareParams`

```typescript
interface CallHierarchyPrepareParams extends TextDocumentPositionParams,
    WorkDoneProgressParams {
}
// Inherits: textDocument (TextDocumentIdentifier), position (Position)
```

**Response:**

- **Result:** `CallHierarchyItem[] | null`

`null` means no call hierarchy item at this position (not a callable symbol).

```typescript
interface CallHierarchyItem {
    name: string;                   // REQUIRED - e.g. "myFunction"
    kind: SymbolKind;               // REQUIRED - e.g. Function=12, Method=6
    tags?: SymbolTag[];             // optional
    detail?: string;                // optional - e.g. function signature
    uri: DocumentUri;               // REQUIRED - file URI
    range: Range;                   // REQUIRED - full range of the symbol
    selectionRange: Range;          // REQUIRED - range to select (name)
                                    // Must be contained by `range`
    data?: LSPAny;                  // preserved between prepare and
                                    // incoming/outgoing calls requests
}
```

### 3b. callHierarchy/incomingCalls

**Method:** `callHierarchy/incomingCalls` (since 3.16)

**Params:** `CallHierarchyIncomingCallsParams`

```typescript
interface CallHierarchyIncomingCallsParams extends WorkDoneProgressParams,
    PartialResultParams {
    item: CallHierarchyItem;         // REQUIRED - the item from prepareCallHierarchy
}
```

**Response:**

- **Result:** `CallHierarchyIncomingCall[] | null`

```typescript
interface CallHierarchyIncomingCall {
    from: CallHierarchyItem;         // REQUIRED - who calls this function
    fromRanges: Range[];             // REQUIRED - where in the caller the
                                     // call appears
}
```

### 3c. callHierarchy/outgoingCalls

**Method:** `callHierarchy/outgoingCalls` (since 3.16)

**Params:** `CallHierarchyOutgoingCallsParams`

```typescript
interface CallHierarchyOutgoingCallsParams extends WorkDoneProgressParams,
    PartialResultParams {
    item: CallHierarchyItem;         // REQUIRED - the item from prepareCallHierarchy
}
```

**Response:**

- **Result:** `CallHierarchyOutgoingCall[] | null`

```typescript
interface CallHierarchyOutgoingCall {
    to: CallHierarchyItem;           // REQUIRED - who this function calls
    fromRanges: Range[];             // REQUIRED - where in THIS function
                                     // the call happens
}
```

### Capabilities

**Client capability** (in `initialize` params `capabilities.textDocument.callHierarchy`):

```typescript
interface CallHierarchyClientCapabilities {
    dynamicRegistration?: boolean;
}
```

**Server capability** (in `initialize` response):
- Property path: `callHierarchyProvider`
- Type: `boolean | CallHierarchyOptions | CallHierarchyRegistrationOptions`

```typescript
interface CallHierarchyOptions extends WorkDoneProgressOptions {
}
```

`incomingCalls` and `outgoingCalls` do NOT define their own capabilities --
they are only available if the server registers `callHierarchyProvider`.

### Edge Cases

- **prepareCallHierarchy returns null:** Position doesn't have a callable
  symbol (e.g., on a variable, comment, whitespace). Return empty result.
- **prepareCallHierarchy returns empty array:** Valid position but no item
  resolved. Treat same as null.
- **incomingCalls/outgoingCalls return null:** No callers/callees found.
  Treat as empty list.
- **Incoming/outgoing calls require the FULL CallHierarchyItem** from prepare
  as the `item` param. This is a two-step process:
  1. `prepareCallHierarchy` -> get CallHierarchyItem(s)
  2. Pass item(s) to `incomingCalls` and/or `outgoingCalls`
- **Multiple items from prepare:** A position could resolve to multiple
  overloaded functions. Each is a separate CallHierarchyItem. Should query
  incoming/outgoing for each.
- **The `data` field:** Servers may attach opaque data to CallHierarchyItem
  that must be preserved when passing it to incomingCalls/outgoingCalls.
  DO NOT strip or modify this field.
- **Recursive calls:** A function calling itself will appear in both
  incoming and outgoing calls. Not an error, just needs to be handled in
  presentation.

### Implementation Notes

- Requires document to be open first.
- Two-step flow: prepare then fetch incoming/outgoing.
- The `item` parameter for incoming/outgoing must be the EXACT object
  returned by prepare, including the `data` field if present.
- Our tool should combine all three requests into a single tool call with
  parameters `direction` (incoming, outgoing, or both).

---

## 4. Required Changes to ServerSession.start() Initialize Capabilities

### Current State

The current `ServerSession.start()` in `lsp_manager.py` sends:

```python
"capabilities": {
    "textDocument": {
        "publishDiagnostics": {"relatedInformation": True},
        "definition": {"linkSupport": True},
    }
}
```

### Needed Changes

For the three new methods, here is what needs to be added to client
capabilities in the `initialize` request:

#### For workspace/symbol

Add to `capabilities`:
```python
"workspace": {
    "symbol": {
        "dynamicRegistration": False,
        "symbolKind": {
            "valueSet": list(range(1, 27))  # All SymbolKinds 1-26
        },
        "tagSupport": {
            "valueSet": [1]  # SymbolTag.Deprecated = 1
        },
    }
}
```

**Impact:** LOW. The server will provide workspace/symbol regardless of
whether we declare this capability. Declaring it tells the server we can
handle extended SymbolKinds and tags. Without it, some servers may limit
the response. **Recommended to add but not strictly required.**

#### For textDocument/rename

Add to `capabilities.textDocument`:
```python
"rename": {
    "dynamicRegistration": False,
    "prepareSupport": True,          # we could support prepareRename later
    "honorsChangeAnnotations": False,
}
```

And ensure `workspace.workspaceEdit` capability exists:
```python
"workspace": {
    "workspaceEdit": {
        "documentChanges": True,     # we can handle documentChanges format
    }
}
```

**Impact:** MEDIUM. Some servers check for rename capability before enabling
the rename provider. The `documentChanges` capability in workspaceEdit tells
the server it can return the more structured format. **Recommended to add.**

#### For textDocument/prepareCallHierarchy

Add to `capabilities.textDocument`:
```python
"callHierarchy": {
    "dynamicRegistration": False,
}
```

**Impact:** MEDIUM. Some servers require the client to declare call hierarchy
support before enabling the provider. **Recommended to add.**

### Recommended Updated Capabilities Block

```python
"capabilities": {
    "textDocument": {
        "publishDiagnostics": {"relatedInformation": True},
        "definition": {"linkSupport": True},
        "rename": {
            "dynamicRegistration": False,
            "prepareSupport": True,
            "honorsChangeAnnotations": False,
        },
        "callHierarchy": {
            "dynamicRegistration": False,
        },
    },
    "workspace": {
        "symbol": {
            "dynamicRegistration": False,
            "symbolKind": {
                "valueSet": list(range(1, 27)),
            },
            "tagSupport": {
                "valueSet": [1],
            },
        },
        "workspaceEdit": {
            "documentChanges": True,
        },
    },
}
```

---

## 5. Summary of Key Design Decisions

### Tool Parameter Patterns

Following the existing pattern from `lsp_goto_definition` and
`lsp_find_references`:

| Tool | Required Params | Optional Params |
|------|----------------|-----------------|
| `lsp_find_symbol` | `query` | `force_refresh` |
| `lsp_rename_symbol` | `filename`, `line`, `character`, `new_name` | `force_refresh` |
| `lsp_call_hierarchy` | `filename`, `line`, `character` | `direction` (incoming/outgoing/both, default both), `force_refresh` |

### Response Format Patterns

- `lsp_find_symbol`: `{ success, symbols: [{name, kind, kind_name, location, container_name}] }`
- `lsp_rename_symbol`: `{ success, edits: [{file, edits: [{range, newText}]}] }` or error
- `lsp_call_hierarchy`: `{ success, items: [{name, kind, location}], incoming: [{from, fromRanges}], outgoing: [{to, fromRanges}] }`

### Important Implementation Considerations

1. **workspace/symbol does NOT require a file to be open** -- it's workspace-wide.
   But we still need a session (server process) running. We can use any file in
   the workspace to derive the workspace root and language config.

2. **rename and call hierarchy DO require the document to be open** via
   `textDocument/didOpen` first. Our `session.open_document()` handles this.

3. **Call hierarchy is a multi-request flow.** Our `lsp_call_hierarchy` tool
   must chain: prepare -> incoming/outgoing. The `CallHierarchyItem.data`
   field must be preserved between calls.

4. **Rename returns edits, not applied changes.** Our tool returns the edit
   description. The calling agent can then apply edits via `patch` if desired.

5. **All three methods can return null.** Handle gracefully with success=True
   and an empty results array + message.
