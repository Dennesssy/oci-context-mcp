# Agent Working Guide

Instructions for AI agents and contributors picking up tasks in this repository.

---

## Task System Overview

Tasks live as markdown files in three directories:

```
docs/
  todo/       Active tasks — pick up from here
  archived/   Completed or cancelled tasks — read-only reference
  Main/       Architecture and design documents — do not modify without review
```

Each task file is a single unit of work. One task = one file.

---

## Task File Format

Every task file in `docs/todo/` follows this structure:

```markdown
# Task Title

**Status:** pending | in_progress | blocked | review
**Owner:** unassigned | agent | @username
**Blocked by:** (task filename or "nothing")
**Priority:** critical | high | medium | low

## Context
Why this task exists. What problem it solves.

## Acceptance Criteria
- [ ] Specific measurable outcome 1
- [ ] Specific measurable outcome 2
- [ ] Tests pass or verification step completed

## Technical Notes
Implementation hints, relevant files, prior decisions.

## References
Links to related tasks, docs, PRs, or external resources.
```

---

## How to Triage

When starting a session with no assigned task:

1. List all files in `docs/todo/`
2. Read each task's `Status`, `Priority`, and `Blocked by` fields
3. Skip any task with `Status: in_progress` or `Owner:` already set
4. Skip any task with `Blocked by:` pointing to an incomplete task
5. Select the highest-priority unblocked, unowned task
6. Read `docs/Main/ARCHITECTURE.md` for context if the task touches infrastructure
7. Read relevant source files before writing any code

Priority order: `critical` > `high` > `medium` > `low`

When two tasks have equal priority, prefer the one with the most dependents (check which other tasks list it in `Blocked by`).

---

## How to Pick Up a Task

1. Open the task file in `docs/todo/`
2. Change `Status:` to `in_progress`
3. Change `Owner:` to `agent` (or your identifier)
4. Commit the status update before starting work:
   ```
   chore(docs): claim task <filename>
   ```
5. Work the acceptance criteria in order
6. Do not mark complete until all checkboxes are checked

---

## How to Mark Complete

1. Verify every acceptance criterion checkbox is checked
2. Move the file from `docs/todo/` to `docs/archived/`:
   ```bash
   git mv docs/todo/<task>.md docs/archived/<task>.md
   ```
3. Add a completion note at the bottom of the file:
   ```markdown
   ## Completed
   **Date:** YYYY-MM-DD
   **Summary:** One sentence describing what was done and any deviations.
   **PR / Commit:** <sha or link>
   ```
4. Commit both the code changes and the archive move in the same commit

---

## How to Handle Blockers

If a task cannot proceed:

1. Change `Status:` to `blocked`
2. Update `Blocked by:` with the blocking task filename or external dependency
3. Add a `## Blocker Note` section explaining what is needed
4. Commit the status update
5. Move to the next available unblocked task
6. Do not leave a task `in_progress` if it is blocked — revert to `blocked`

---

## How to Create a New Task

When work is discovered mid-session that is out of scope for the current task:

1. Create a new file in `docs/todo/` with a descriptive kebab-case name
2. Fill in the full task format above
3. Set `Status: pending`, `Owner: unassigned`
4. If it blocks the current task, add it to `Blocked by:` in the current task
5. Commit the new task file before continuing

Filename convention: `NN-short-description.md` where `NN` is the next available number.

---

## Agent-Specific Rules

- Always read the task file fully before writing any code
- Always read the files you intend to modify before modifying them
- Do not modify files listed in `docs/Main/` without an explicit task authorizing it
- Do not commit secrets, `.env` files, `*.pem` keys, or `terraform.tfvars` with real values
- Do not squash or force-push commits — the history is the audit trail
- If the acceptance criteria are ambiguous, add a `## Questions` section and stop — do not guess
- Telemetry in `oci_mcp_metrics.jsonl` is local only — never commit it
- After completing a task, check `docs/todo/` for tasks that were blocked by it and update their `Blocked by:` field

---

## Repository Map

```
mcp_server.py          Main server — 69 tools, OCIAuthManager, FastMCP
pyproject.toml         Package metadata, entry point (oci-mcp = mcp_server:cli)
requirements.txt       Runtime dependencies
Dockerfile             ARM64 image for OCI A1.Flex (CI.Standard.A1.Flex)
.dockerignore          Excludes secrets, logs, IaC from image
infra/
  main.tf              ARCSEM stack — full Terraform (all 6 layers)
  variables.tf         Input variables
  outputs.tf           mcp_server_url, api_gateway_id, log_group_id
  provider.tf          OCI Terraform provider
scripts/
  push_to_ocir.sh      Build ARM64 image and push to OCIR
docs/
  agents.md            This file
  todo/                Active tasks
  archived/            Completed tasks
  Main/                Architecture documents
tests/                 Test suite (pending population)
terminal_test/         Local animation scaffolding — not production code
```

---

## Key Design Decisions

These are locked. Do not change without creating a task and getting explicit confirmation:

| Decision | Rationale |
|----------|-----------|
| ARM64 Docker image (CI.Standard.A1.Flex) | OCI free tier — avoids ~$15/mo on E4.Flex |
| Private subnet, no public IP on Container Instance | Security — API Gateway is the only public ingress |
| Cross-tenancy IAM (not API key storage) for multi-tenant | Zero credential liability |
| Instance Principal for own-tenancy operations | Keyless, revocable, Oracle-recommended |
| `OCIAuthManager` will become per-request (v3.0) | Required for multi-tenancy — do not add new singleton dependencies |
| `allow_origins=["*"]` is temporary | Will be locked to API Gateway hostname when multi-tenant auth ships |
