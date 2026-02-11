# AGENTS.md — Codex Instructions

## Current Task: LiveContextManager

Build `silas/core/context_manager.py` implementing the `ContextManager` protocol from `silas/protocols/context.py`.

Read specs.md Section 3.5 (Context models) and Section 4.3 (ContextManager protocol) and Section 5.7 (eviction — stub for now).

### What to Build

`LiveContextManager` class:
- Stores context items per scope in memory (dict[str, list[ContextItem]])
- Zone management: add, drop, get_zone
- Subscription tracking: subscribe, unsubscribe (basic — no materialization yet)
- Profile management: set_profile per scope, track active profile name
- `render(scope_id, turn_number)` — render all zones as metadata-tagged string per spec format:
  ```
  --- zone | turn N | source ---
  content
  --- end ---
  ```
  Rendering order: system → chronicle → memory → workspace
- Observation masking: tool_result items older than `observation_mask_after_turns` get content replaced with placeholder
- `enforce_budget(scope_id, turn_number, current_goal)` — Phase 1c: heuristic eviction only (no scorer). Drop lowest-relevance items from over-budget zones. Return list of evicted ctx_ids.
- `token_usage(scope_id)` — return dict of zone → total tokens

### Constructor
```python
def __init__(self, token_budget: TokenBudget, token_counter: HeuristicTokenCounter):
```

### Dependencies
- `silas.models.context`: ContextItem, ContextZone, ContextProfile, ContextSubscription, TokenBudget
- `silas.core.token_counter`: HeuristicTokenCounter

### Rules
- No async needed — protocol methods are sync
- Use `datetime.now(timezone.utc)` for all timestamps
- Observation masking: replace content with `[Result of {source} — {token_count} tokens — see memory for details]`
- Budget enforcement: heuristic only (evict lowest relevance first, oldest first as tiebreaker)
- Run `ruff check` and `pytest` before finishing

When completely finished, run:
openclaw gateway wake --text "Done: LiveContextManager built" --mode now
