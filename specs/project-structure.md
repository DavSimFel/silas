## 1. Project Structure

```
silas/
├── pyproject.toml
├── silas/
│   ├── __init__.py
│   ├── main.py                     # Entry point, CLI
│   ├── config.py                   # Pydantic Settings, YAML config loading
│   │
│   ├── protocols/                  # ALL interfaces (Protocol classes)
│   │   ├── __init__.py
│   │   ├── channels.py             # ChannelAdapterCore, RichCardChannel, ChannelMessage
│   │   ├── agents.py               # Router, Planner, AgentResponse
│   │   ├── memory.py               # MemoryStore, MemoryRetriever, MemoryConsolidator, MemoryPortability
│   │   ├── context.py              # ContextManager, ContextSubscription
│   │   ├── approval.py             # ApprovalVerifier, NonceStore
│   │   ├── execution.py            # EphemeralExecutor, SandboxManager
│   │   ├── gates.py                # Gate, GateRunner, GateCheckProvider
│   │   ├── work.py                 # WorkItemExecutor, VerificationRunner, WorkItemStore
│   │   ├── scheduler.py            # TaskScheduler, ScheduledTask
│   │   ├── audit.py                # AuditLog
│   │   ├── personality.py          # PersonalityEngine, PersonaStore
│   │   ├── proactivity.py          # SuggestionEngine, AutonomyCalibrator
│   │   ├── connections.py          # ConnectionManager (thin coordinator over connection skills)
│   │   └── skills.py              # SkillLoader, SkillResolver
│   │
│   ├── models/                     # Pydantic data models (no logic)
│   │   ├── __init__.py
│   │   ├── messages.py             # SignedMessage, ChannelMessage, TaintLevel
│   │   ├── agents.py               # AgentResponse, PlanAction, RouteDecision
│   │   ├── memory.py               # MemoryItem, MemoryQuery, MemoryOp
│   │   ├── context.py              # ContextItem, ContextZone, ContextSubscription, ContextProfile, TokenBudget
│   │   ├── approval.py             # ApprovalToken, ApprovalVerdict, ApprovalScope
│   │   ├── execution.py            # ExecutionEnvelope, ExecutionResult
│   │   ├── work.py                 # WorkItem, WorkItemType, StepContract, Budget
│   │   ├── gates.py                # Gate, GateType, Expectation, AccessLevel
│   │   ├── sessions.py             # Session, SessionType
│   │   ├── skills.py               # SkillMetadata (parsed SKILL.md frontmatter), SkillRef
│   │   ├── personality.py          # PersonalityAxis, VoiceConfig, PersonaState
│   │   ├── review.py               # BatchProposal, DraftReview, Suggestion, AutonomyThresholdProposal models
│   │   └── connections.py          # AuthStrategy, SetupStep, SecureInputRequest, HealthCheckResult, ConnectionFailure
│   │
│   ├── core/                       # Core implementations
│   │   ├── __init__.py
│   │   ├── stream.py               # The Stream — main loop, persistent session
│   │   ├── work_executor.py        # WorkItemExecutor — universal task runner
│   │   ├── plan_parser.py          # Parse markdown plans into WorkItems
│   │   ├── verification_runner.py  # External verification — agent cannot touch
│   │   ├── gate_runner.py          # Gate evaluation — dispatches to providers
│   │   ├── access_controller.py    # Access level state machine
│   │   ├── context_manager.py      # ContextManager — zones, profiles, two-tier eviction, subscriptions
│   │   ├── key_manager.py          # Ed25519 key generation, signing, verification
│   │   ├── approval_engine.py      # Approval lifecycle, verification, nonce tracking
│   │   ├── turn_context.py         # TurnContext dependency container for TurnProcessor
│   │   ├── taint_tracker.py        # Input taint propagation
│   │   ├── token_counter.py        # Heuristic token counter (chars ÷ 3.5)
│   │   ├── personality_engine.py   # Axes composition, context detection, mood drift
│   │   └── suggestion_engine.py    # Proactive next-step suggestions + autonomy threshold proposals
│   │
│   ├── agents/                     # PydanticAI Agent definitions
│   │   ├── __init__.py
│   │   ├── proxy.py                # ProxyAgent — proxy-tier routing, direct response
│   │   ├── planner.py              # PlannerAgent — planner-tier planning, reasoning
│   │   ├── executor.py             # ExecutorAgent — cheapest, runs briefings
│   │   └── prompts/
│   │       ├── proxy_system.md
│   │       ├── planner_system.md
│   │       └── constitution.md
│   │
│   ├── memory/                     # Memory implementations
│   │   ├── __init__.py
│   │   ├── sqlite_store.py         # SQLite + FTS5 (sqlite-vec enabled in Phase 3)
│   │   ├── consolidator.py         # Background memory consolidation
│   │   ├── retriever.py            # Multi-graph retrieval
│   │   ├── embedder.py             # fastembed wrapper
│   │   └── migrations/
│   │       ├── 001_initial.sql
│   │       └── 002_vector.sql      # Adds sqlite-vec indexes
│   │
│   ├── execution/                  # Ephemeral executor implementations
│   │   ├── __init__.py
│   │   ├── registry.py             # ExecutorRegistry (core primitives only)
│   │   ├── subprocess_sandbox.py   # SubprocessSandboxManager (default backend)
│   │   ├── docker_sandbox.py       # DockerSandboxManager (optional backend)
│   │   ├── shell.py                # ShellExecutor (runs via configured sandbox backend)
│   │   ├── python_exec.py          # PythonExecutor (runs via configured sandbox backend)
│   │   └── web_search.py           # WebSearchExecutor (provider-backed retrieval, key-gated)
│   │
│   ├── gates/                      # Gate check providers
│   │   ├── __init__.py
│   │   ├── guardrails_ai.py        # GuardrailsAIChecker (validator hub adapter)
│   │   ├── predicate.py            # PredicateChecker (numeric, regex, exit code)
│   │   ├── llm.py                  # LLMChecker (subjective quality checks via configured quality-tier model)
│   │   ├── script.py               # ScriptChecker (custom shell scripts)
│   │   └── guards/                 # Custom domain-specific check scripts
│   │       ├── content_policy.py
│   │       ├── data_redaction.py
│   │       └── verify_customer.py
│   │
│   ├── channels/
│   │   ├── __init__.py
│   │   ├── web.py                  # WebChannel (FastAPI + WebSocket, primary)
│   │   ├── telegram.py             # TelegramChannel (optional)
│   │   └── cli.py                  # CLIChannel (dev/debug)
│   │
│   ├── persistence/                # Durable storage for runtime state
│   │   ├── __init__.py
│   │   ├── work_item_store.py      # SQLite WorkItem persistence
│   │   ├── chronicle_store.py      # SQLite chronicle persistence
│   │   ├── memory_portability.py   # Memory export/import bundle adapter
│   │   └── persona_store.py        # SQLite personality state/event persistence
│   │
│   ├── skills/
│   │   ├── __init__.py
│   │   ├── loader.py               # SKILL.md frontmatter parser + directory scanner
│   │   ├── resolver.py             # Resolves skill names to paths, validates script access
│   │   ├── validator.py            # Deterministic skill validation checks
│   │   ├── importer.py             # External skill adaptation + transform report
│   │   ├── coding/                 # Shipped default skill: coding/build/test workflows
│   │   ├── skill-maker/            # Shipped default skill: build/install new skills
│   │   └── {custom-skill}/         # Flat skill namespace (user-installed or generated)
│   │
│   ├── connections/
│   │   ├── __init__.py
│   │   ├── manager.py              # Connection lifecycle coordinator (invokes connection-skill scripts)
│   │   └── registry.py             # Connection state persistence + lookup
│   │
│   ├── audit/
│   │   └── sqlite_audit.py         # Hash-chained SQLite audit log
│   │
│   └── scheduler/
│       └── ap_scheduler.py         # APScheduler wrapper
│
├── config/
│   └── silas.yaml                   # Default configuration
│
├── web/                            # Web frontend (served by FastAPI)
│   ├── index.html                  # PWA shell
│   ├── style.css
│   ├── app.js                      # WebSocket + card renderer
│   ├── manifest.json               # Home-screen install metadata
│   ├── sw.js                       # Install/runtime caching hooks
│
├── tests/
│   ├── conftest.py                 # Shared fixtures
│   ├── fakes.py                    # In-memory implementations for testing
│   ├── test_context.py
│   ├── test_gates.py
│   ├── test_work_executor.py
│   ├── test_verification.py
│   ├── test_approval.py
│   ├── test_memory.py
│   ├── test_personality.py
│   ├── test_proactivity.py
│   └── evals/                      # Pydantic Evals
│       ├── eval_routing.py
│       ├── eval_planning.py
│       └── eval_memory.py
│
└── plans/                          # Example plan templates
    ├── fix-bug.md
    ├── customer-support-bot.md
    └── health-monitor.md
```

---

### 1.5 Onboarding Flow (First Run)

The first-run flow is phone-first, PWA-compatible, and requires no desktop-only steps:

1. **Welcome card**
   - Copy: "I'm Silas. Let's get you set up."
   - CTA: `[Get Started]`
2. **LLM provider selection**
   - Options: OpenRouter, local
   - Single API-key field (`OPENROUTER_API_KEY`) with immediate validation
3. **Identity bootstrap**
   - Name, primary email, primary phone
   - Used for connection discovery and domain policy defaults
4. **Completion**
   - Redirect to The Stream
   - First message: "I'm ready. Tell me what to connect first, or I'll figure it out."

### 1.6 Web Frontend (PWA)

The web frontend is a Progressive Web App following the **Quiet** design language (see `specs-design.md` for full specification).

- `web/manifest.json` enables install-to-home-screen
- `web/sw.js` provides install/runtime caching hooks
- Design language: Quiet — agent interface, not chat app. Glass surfaces, progressive disclosure, physics-based motion.
- Responsive layouts:
  - Phone (<640px): single-column stream, bottom sheets
  - Tablet (640-1024px): wider stream, overlay panels
  - Desktop (>1024px): centered stream (760px max) + side panel

Required interactive views/components:

- **Stream** — conversation feed (user inputs right-aligned minimal, agent responses full-width no containers)
- **Cards** — glass surfaces for approvals, drafts, decisions (per §0.5.3 card contract)
- **Status strip** — ambient work awareness (top, invisible when idle)
- **Work panel** — bottom sheet with active/pending work items
- **Composer** — minimal input, auto-growing, slash commands
- **Side panel** — desktop-only, reference material and memory (hidden by default)
- Activity Log view (human-readable audit timeline)

