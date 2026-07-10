# Multi-Case Formula Results Design

## Goal

Present Branch Currents, Node Equations, and Reduced results using the same global profiles and `case_id` ordering as Multi-Case C Export, without requiring users to change the active case in the component editor.

## Scope

- Branch Currents gains a profile selector and displays one profile at a time.
- Node Equations displays every profile sequentially.
- Reduced displays every profile sequentially and renders each profile as soon as its analysis finishes.
- Multi-Case C Export remains the source of truth for profile definitions, ordering, labels, and case assignments.
- Model JSON, Python Draft, optimized elimination, and C export behavior are unchanged.

## Profile Source

The formula views consume the normalized profile list already used by Multi-Case C Export. A profile contains:

- the global `case_id`;
- the optional profile name;
- the selected switch-case index for each eligible branch or packaged black box;
- a concise assignment summary for display.

When no multi-case profiles exist, all three views preserve their existing single-case behavior and do not show extra profile controls or headings.

Invalid profile text is handled consistently with Multi-Case C Export. Formula views show the localized validation error instead of silently falling back to the editor's active case.

## Isolated Evaluation Context

Formula evaluation must not mutate persistent editor state. A helper evaluates a synchronous callback under one profile:

1. Record every affected branch's current `activeSwitchCase`.
2. Apply the profile's case assignments in memory.
3. Build formulas, assembled systems, or backend payloads.
4. Restore every recorded case in a `finally` block.

The helper must not call `commitHistory()`, `render()`, persistence functions, or model-export functions. Backend requests receive fully materialized payloads, so editor state can be restored before awaiting the request.

## Branch Currents

For multi-case circuits, the top of the formula list contains a compact `Case` select. Its options use `case_id`, profile name, and the assignment summary. The selected profile index lives in transient UI state and is clamped whenever profiles change.

The original branch-current cards are evaluated under the selected profile. If reduced branch currents are enabled, their reduction payload is also built under that same profile. Cache keys include the materialized payload, preventing results from leaking between profiles.

Changing the select rerenders only the output view. It does not change component editor fields, canvas state, undo history, or exported model JSON.

## Node Equations

Node Equations evaluates each profile synchronously and renders profile sections in profile order. Every section contains:

- a heading with `case_id` and profile name;
- a concise branch-case assignment summary;
- the existing node-equation presentation for that profile.

One profile's rendering error appears inside that profile section and does not suppress later sections.

## Reduced Results

Reduced analysis is sequential and progressive:

1. Render placeholders for all profiles in profile order.
2. Build the first profile payload in the isolated evaluation context.
3. Use its cache entry or await its reduction request.
4. Replace only that profile's placeholder with its rendered result or error.
5. Continue with the next profile while the Reduced tab and render token remain current.

This intentionally avoids `Promise.all`. It limits concurrent SymPy load, makes progress visible, and prevents the interface from waiting for the slowest profile before showing useful output.

Each profile has an independent DOM target, cache key, loading state, result, and error state. A failed profile does not stop subsequent profiles. Switching away invalidates the render token, so stale completions do not overwrite another output tab.

## Presentation

Profile sections use the existing output-panel visual language. Case headings are compact and scan-friendly; they are not nested cards. Branch Currents uses a native select because it is an option set. Node Equations and Reduced remain vertically scrollable result documents.

## Testing

Frontend tests cover:

- normalized profile order and labels;
- isolated evaluation restoring active cases after success and exceptions;
- Branch Currents selector rendering and profile-specific formulas;
- Node Equations rendering every profile in order;
- Reduced issuing requests sequentially and updating one profile target at a time;
- per-profile error isolation;
- unchanged single-case behavior;
- absence of persistent editor-state mutation.

Existing backend reduction and multi-case code-generation tests remain unchanged and must continue to pass.
