## 19. Context Eviction Feedback Loop

### 19.1 Architecture

Two-tier eviction (§5.7) already separates heuristic pre-filtering from scorer-model eviction. This section extends that with a **feedback collection layer** that turns eviction overrides into calibration data.

**Role separation:**

| Actor | Responsibility |
|---|---|
| Heuristic / cheap scorer model | Continuously scores chunks, proposes eviction candidates (background, never burns main-LLM tokens) |
| Main LLM | Triggers flush when it needs context space — does NOT decide what to evict |
| Main LLM (at flush time) | May **exclude** items from the proposed eviction list (keep-backs) |
| Feedback store | Records every exclusion as a training signal for scorer calibration |

The cheap scorer (configured via `models.scorer`, default `claude-haiku-4-5`) runs asynchronously. It maintains a ranked eviction queue per scope. When the main LLM triggers a flush (explicitly or via token pressure at turn step 5), the system presents the eviction candidates. The LLM can exclude items it still needs — those exclusions are the feedback signal.

### 19.2 Data Models

```python
class EvictionProposal(BaseModel):
    """A batch of chunks proposed for eviction by the scorer."""
    proposal_id: str              # Unique ID for this proposal batch
    scope_id: str
    turn_number: int
    proposed_at: datetime
    scorer_model: str             # Model that scored (e.g. "claude-haiku-4-5")
    candidates: list[EvictionCandidate]

class EvictionCandidate(BaseModel):
    """A single chunk proposed for eviction."""
    ctx_id: str
    zone: ContextZone
    relevance_score: float        # Scorer's relevance estimate (0.0 = irrelevant, 1.0 = critical)
    age_turns: int                # Turns since last reference
    token_count: int

class EvictionFeedback(BaseModel):
    """Recorded when an eviction proposal is resolved."""
    proposal_id: str
    scope_id: str
    resolved_at: datetime
    resolved_by: str              # "llm" | "user" | "auto" (no exclusions, timeout)
    evicted: list[str]            # ctx_ids actually evicted
    excluded: list[EvictionExclusion]  # ctx_ids kept back

class EvictionExclusion(BaseModel):
    """A single keep-back: scorer wanted to evict, but it was overridden."""
    ctx_id: str
    relevance_score: float        # Scorer's original score (what it thought)
    exclude_reason: str | None    # Optional reason from LLM ("still needed for X")
```

### 19.3 Feedback Collection Protocol

1. Scorer produces `EvictionProposal` with ranked candidates.
2. At flush time, harness presents candidates to main LLM as part of the turn context (lightweight — just `ctx_id` + `relevance_score` + summary snippet).
3. LLM responds with optional `exclude_ctx_ids: list[str]` in its structured output (new field on `AgentResponse`).
4. Harness evicts non-excluded candidates, records `EvictionFeedback` to SQLite.
5. If LLM doesn't respond within `eviction_feedback_timeout_ms` (default 2000), auto-resolve: evict all candidates, record `resolved_by: "auto"`.

### 19.4 Scorer Calibration

Feedback records accumulate in `eviction_feedback` table. Calibration uses them as follows:

- **Exclusion rate by zone**: If the LLM consistently keeps back `chronicle` items scored < 0.3, the scorer's zone weighting is miscalibrated.
- **Exclusion rate by age**: If old items are frequently excluded, the age-decay factor is too aggressive.
- **Per-scope patterns**: Some scopes (e.g., long-running projects) have different retention needs than ephemeral conversations.

Calibration can be:
- **Offline**: Export feedback dataset, fine-tune scorer prompt / heuristic weights, deploy updated config.
- **Online (R14)**: Periodic background job adjusts scorer weights based on rolling exclusion statistics. Requires ablation validation (§20) before enabling.

### 19.5 Ablation Requirements

**No calibration method may be deployed without ablation study.** Specifically:

- Baseline: current heuristic-only eviction (no feedback loop)
- Variant A: feedback-calibrated scorer weights
- Variant B: feedback-calibrated scorer prompt
- Variant C: online auto-adjustment

Each variant must be evaluated on the benchmarking harness (§20) using context quality metrics:
- Retrieval relevance (does the retained context help the LLM answer correctly?)
- Token efficiency (tokens retained vs. task completion rate)
- Exclusion prediction accuracy (can the scorer learn to predict what the LLM would exclude?)

See §20.4 for integration with the benchmarking framework.


## 20. Agent & Skill Benchmarking

### 20.1 Overview

A standardized evaluation framework for measuring agent and skill performance. Covers correctness, efficiency, regression detection, and ablation studies for system components (including eviction calibration per §19).

### 20.2 Data Models

```python
class BenchmarkSuite(BaseModel):
    """A collection of benchmark cases for one agent or skill."""
    suite_id: str
    name: str                     # e.g. "planner-decomposition", "shell-skill-v2"
    target: str                   # Agent or skill identifier being evaluated
    version: str                  # Suite version (SemVer)
    cases: list[BenchmarkCase]
    metadata: dict[str, str]      # Tags: domain, difficulty tier, source

class BenchmarkCase(BaseModel):
    """A single input→expected-output pair."""
    case_id: str
    description: str
    input: BenchmarkInput
    expected: BenchmarkExpected
    tags: list[str]               # ["multi-step", "error-recovery", "edge-case"]
    difficulty: str               # "easy" | "medium" | "hard" | "adversarial"
    source: str                   # "synthetic" | "organic" | "regression"

class BenchmarkInput(BaseModel):
    """Standardized input for a benchmark case."""
    goal: str                     # Natural language goal / prompt
    context: list[dict] | None    # Optional pre-loaded context items
    config_overrides: dict | None # Optional config overrides for this case
    skill_args: dict | None       # For skill-level benchmarks

class BenchmarkExpected(BaseModel):
    """Expected outcome definition — supports multiple evaluation modes."""
    mode: str                     # "exact" | "contains" | "llm_judge" | "metric_threshold" | "human"
    value: str | dict | None      # Expected value (for exact/contains) or judge criteria
    metric_thresholds: dict[str, float] | None  # e.g. {"latency_ms": 5000, "token_cost": 1000}

class BenchmarkRun(BaseModel):
    """A single execution of a suite against a configuration."""
    run_id: str
    suite_id: str
    started_at: datetime
    completed_at: datetime | None
    config_snapshot: dict         # Model, prompt version, scorer weights — full reproducibility
    results: list[BenchmarkResult]
    summary: BenchmarkSummary | None

class BenchmarkResult(BaseModel):
    """Result of a single case execution."""
    case_id: str
    status: str                   # "pass" | "fail" | "error" | "timeout" | "skipped"
    actual_output: str | None
    latency_ms: float
    token_cost: int               # Total tokens consumed
    llm_judge_score: float | None # 0.0–1.0 if mode is llm_judge
    human_label: str | None       # Optional post-hoc human annotation
    error: str | None
    retries: int

class BenchmarkSummary(BaseModel):
    """Aggregate metrics for a run."""
    pass_rate: float
    mean_latency_ms: float
    p95_latency_ms: float
    total_token_cost: int
    mean_quality_score: float | None  # Average llm_judge_score where available
    failure_categories: dict[str, int]  # Error type → count
```

### 20.3 Per-Agent Benchmark Targets

| Agent | Key Metrics | Example Cases |
|---|---|---|
| **Proxy** | Route accuracy, latency, false-positive tool calls | Ambiguous queries, multi-intent messages, adversarial injections |
| **Planner** | Plan quality (step count, dependency correctness), decomposition accuracy | Multi-step goals, goals requiring parallel execution, under-specified goals |
| **Executor** | Task completion rate, sandbox escape attempts, verification pass rate | Shell commands, Python execution, error recovery, permission boundaries |
| **Scorer** | Eviction accuracy (vs. human labels), exclusion prediction, token efficiency | Context windows at various fill levels, mixed-zone content, stale vs. active items |

### 20.4 Per-Skill Benchmarks

Each skill MAY define a `benchmarks/` directory alongside its `SKILL.md`:

```
skills/
  shell/
    SKILL.md
    benchmarks/
      suite.json          # BenchmarkSuite definition
      cases/
        pipe-chain.json
        error-recovery.json
        sudo-boundary.json
```

Skill benchmarks are loaded by the harness and executed in the skill's sandbox environment. Skills without benchmarks are flagged in quality reports but not blocked.

### 20.5 Regression Detection

**Trigger:** Any change to model config, system prompts, scorer weights, or gate predicates.

**Process:**
1. Run all affected suites against the new configuration.
2. Compare against the **baseline run** (last accepted run on `dev`).
3. Flag regressions: any case that was `pass` → `fail`, or where `llm_judge_score` drops > 0.1, or latency increases > 50%.
4. Regression report is appended to PR description.
5. Regressions block merge unless explicitly overridden with `benchmark:accept-regression` label.

**Baseline management:** Each suite stores its last-accepted `BenchmarkRun` as `baseline.json` in the suite directory. Updated on merge to `dev`.

### 20.6 Ablation Study Protocol

For evaluating system changes (eviction calibration §19, prompt tuning, model swaps):

1. **Define variants**: Control (current) + 1–N treatment configurations.
2. **Select suites**: All suites tagged with the affected component.
3. **Run each variant** N times (configurable, default 5) to account for LLM non-determinism.
4. **Statistical comparison**: Mean ± stddev for each metric. Flag significant differences (p < 0.05 where sample size permits).
5. **Report**: Markdown table comparing variants, auto-generated by `silas benchmark ablation` CLI command.

**Hard rule:** No calibration or tuning change ships without an ablation run showing non-negative impact on the relevant suites.

### 20.7 Dataset Sources

| Source | Description | Privacy |
|---|---|---|
| **Synthetic** | Hand-crafted or LLM-generated edge cases | No PII, committed to repo |
| **Organic** | Anonymized real session logs (goal + outcome pairs) | PII-scrubbed, stored in `data/benchmarks/` (gitignored), requires explicit opt-in |
| **Regression** | Auto-generated from production failures | Anonymized, tagged with failure category |

### 20.8 Integration with Monitoring (§18.4)

Benchmark results feed into the monitoring pipeline:
- Suite pass rates exposed as metrics (`silas_benchmark_pass_rate{suite, agent}`)
- Regression alerts integrate with existing alert channels
- Ablation reports stored in `data/ablation/` for historical comparison

### 20.9 CLI Interface

```
silas benchmark run <suite>          # Run a single suite
silas benchmark run --all            # Run all suites
silas benchmark compare <run1> <run2> # Compare two runs
silas benchmark ablation <config1> <config2> [--runs N]
silas benchmark report <run>         # Generate markdown report
silas benchmark baseline <run>       # Set run as new baseline
```
