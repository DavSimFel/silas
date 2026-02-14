## 13. Example Plans

### 13.1 Simple Task — Fix a Bug

```markdown
---
id: task-fix-tz-bug
type: task
title: Fix timezone bug in shift scheduler
interaction_mode: act_and_report
budget: { max_tokens: 200000, max_cost_usd: 2.00, max_wall_time_seconds: 1800 }
verify:
  - name: tests_pass
    run: "pytest tests/test_shifts.py -v"
    expect: { exit_code: 0 }
  - name: lint_clean
    run: "ruff check ."
    expect: { exit_code: 0 }
on_stuck: consult_planner
---

# Context

The shift roster generator produces overlapping shifts when employees
span multiple timezones. Root cause is likely in
`scheduler/slot_allocator.py` — collision detection uses local time
instead of UTC.

# What to do

1. Read `scheduler/slot_allocator.py` and `tests/test_shifts.py`
2. Fix timezone handling — all comparisons in UTC, only convert for display
3. Run tests. Add test_cross_timezone_collision if missing
4. Run `ruff check . --fix` and `pytest tests/ -v`
5. Commit: "fix: timezone collision detection in slot allocator"
6. Push to branch `fix/timezone-slots` and create PR

# Constraints

- Do NOT change the Slot data model
- Do NOT modify existing test fixtures — only add new test cases
- If bug is NOT in slot_allocator.py, stop and consult
```

### 13.2 Chatbot Deployment — Customer Support with Access Control

```markdown
---
id: goal-insurance-support
type: goal
title: Insurance customer support chatbot
agent: stream
interaction_mode: confirm_only_when_required
schedule: always_on

gates:
  - name: toxicity_in
    on: every_user_message
    provider: guardrails_ai
    check: toxicity
    config: { threshold: 0.7 }
    on_block: escalate_human

  - name: toxicity_out
    on: every_agent_response
    provider: guardrails_ai
    check: toxicity
    config: { threshold: 0.3 }
    on_block: suppress_and_escalate

  - name: pii_out
    on: every_agent_response
    provider: guardrails_ai
    check: pii
    config: { entities: ["CREDIT_CARD", "SSN"] }
    on_block: suppress_and_rephrase

  - name: jailbreak_in
    on: every_user_message
    provider: guardrails_ai
    check: jailbreak
    on_block: polite_redirect

  - name: identity_verified
    on: every_user_message
    provider: script
    type: custom_check
    check: "verify_customer"
    config:
      script: "guards/verify_customer.py"
      args_from_env: ["CUSTOMER_NAME", "CUSTOMER_DOB"]
    check_expect: { equals: "verified" }
    on_block: retry_verification

access_levels:
  public:
    description: "General info only"
    tools: [faq_search, product_info]
  verified:
    description: "Customer-specific data"
    tools: [faq_search, product_info, policy_lookup, claim_status]
    requires: [identity_verified]
    expires_after: 900

escalation:
  escalate_human:
    action: transfer_to_queue
    queue: support_l2
    message: "Let me connect you with a colleague."
  suppress_and_escalate:
    action: suppress_and_escalate
    message: "I apologize, let me connect you with a team member."
    fallback: escalate_human
  suppress_and_rephrase:
    action: suppress_and_rephrase
    instruction: "Rephrase without credit card numbers or SSNs. Use masked format."
    max_retries: 2
    fallback: escalate_human
  polite_redirect:
    action: respond
    message: "I'm here to help with insurance questions. How can I assist you?"
  retry_verification:
    action: respond
    message: "I couldn't verify those details. Could you double-check?"
---

# Insurance Support Bot

You are a friendly support agent for ACME Insurance.

## Before verification
Answer general questions about products, coverage types, pricing.
If a customer asks about their policy or claims, ask for their
full name and date of birth first.

## After verification
Address the customer by name. Look up policies, claims, coverage.
Never read out full account numbers, SSNs, or payment details.
Use masked formats like "ending in 4821."
```

Note: The `identity_verified` gate uses the `config.args_from_env` pattern rather than command-line interpolation. The script checker passes customer name and date of birth as environment variables (`CUSTOMER_NAME`, `CUSTOMER_DOB`) to the verification script, avoiding shell injection.

### 13.3 Trading Bot — Prediction Market with Sentiment Gates

```markdown
---
id: task-trade-weather
type: task
title: Execute weather prediction trade on Kalshi
interaction_mode: confirm_only_when_required
budget: { max_tokens: 100000, max_cost_usd: 1.50, max_wall_time_seconds: 600 }

gates:
  - name: confidence_check
    on: after_step
    after_step: 1
    provider: predicate
    type: numeric_range
    extract: confidence_score
    auto_approve: { min: 0.75, max: 1.0 }
    require_approval: { min: 0.5, max: 0.75 }
    block: { outside: [0.0, 1.0] }
    on_block: abort_trade

  - name: position_size
    on: after_step
    after_step: 2
    provider: predicate
    type: numeric_range
    extract: position_usd
    auto_approve: { min: 0.0, max: 25.0 }
    require_approval: { min: 25.0, max: 100.0 }
    block: { outside: [0.0, 100.0] }
    on_block: abort_trade

verify:
  - name: order_placed
    run: "python scripts/check_kalshi_order.py --order-id '$order_id'"
    expect: { equals: "filled" }

escalation:
  abort_trade:
    action: report
    message: "Trade aborted — value outside safe range."
---

# Weather Prediction Trade

## Step 1: Analyze
Query the ECMWF and GFS weather models for tomorrow's high
temperature forecast for the target city. Compare with the
Kalshi market price. Output your confidence_score (0.0-1.0).

## Step 2: Size
If confident, calculate position size using Kelly criterion
with half-Kelly sizing. Output position_usd.

## Step 3: Execute
Place the order on Kalshi via API. Output order_id.

## Constraints
- Never exceed $100 per trade
- Only trade weather markets, not politics or entertainment
- If models disagree by more than 5 degrees F, reduce confidence by 0.2
```

### 13.4 Recurring Goal — Health Monitor

```markdown
---
id: goal-health-monitor
type: goal
title: Prediction bot health monitor
interaction_mode: act_and_report
schedule: "*/30 * * * *"
budget: { max_tokens: 10000, max_cost_usd: 0.10, max_wall_time_seconds: 120 }

verify:
  - name: api_responding
    run: "curl -sf https://api.kalshi.com/v1/health | jq -r .status"
    expect: { equals: "ok" }
    network: true
  - name: bot_process_alive
    run: "pgrep -f prediction_bot"
    expect: { exit_code: 0 }
  - name: last_trade_recent
    run: "python scripts/check_last_trade_age.py"
    expect: { output_lt: 3600 }

on_failure: spawn_task
failure_context: |
  The prediction bot health check failed.

  Failed checks:
  $failed_checks

  Investigate and fix. If the process is dead, restart it.
  If the API is down, wait and re-check in 5 minutes.
  If no trades in >1 hour, check if markets are open.
---

Health monitor for the prediction market trading bot.
Runs every 30 minutes. If any check fails, spawns a fix task.
Standing approval (verified per-execution) covers spawned tasks within budget.
```

### 13.5 Project — Multi-Task Deployment

```markdown
---
id: project-deploy-v2
type: project
title: Deploy v2.0 to production
interaction_mode: act_and_report
budget: { max_tokens: 500000, max_cost_usd: 10.00, max_wall_time_seconds: 7200 }

tasks:
  - task-run-tests
  - task-build-image
  - task-deploy-staging
  - task-smoke-test
  - task-deploy-prod

verify:
  - name: prod_healthy
    run: "curl -sf https://api.example.com/health"
    expect: { equals: "ok" }
    network: true
---

Deploy version 2.0. Tasks execute in dependency order.
Each task has its own verification. Project-level check
confirms production is healthy after all tasks complete.
```

---

