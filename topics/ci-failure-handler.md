---
id: ci-failure-handler
name: CI Failure Handler
scope: project
agent: executor
status: active
triggers:
  - source: github
    event: check_suite.completed
    filter:
      conclusion: failure
soft_triggers:
  - keywords: ["ci", "build", "failure", "pipeline"]
    entity: github
approvals:
  - tool: codex_exec
    constraints:
      max_runtime: 300
---

# CI Failure Handler

When CI fails on a pull request or push:

1. Fetch the workflow run logs via GitHub API
2. Identify the failing step and error message
3. Check if the failure is flaky (compare with recent runs)
4. If deterministic: propose a fix on the feature branch
5. If flaky: document in the flaky-tests tracker and re-run

**Do NOT merge or push to protected branches without approval.**
