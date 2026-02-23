# OCI Context MCP Server — Build Plan

**Baseline:** 28 read-only tools, single region, HTTP transport, ~13% OCI CLI coverage
**Target:** Full-featured OCI MCP server at parity with Azure MCP / Cloudflare Workers MCP

---

## Feature Overview

| # | Feature | Phase | Effort | Unblocks |
|---|---------|-------|--------|---------|
| F1 | STDIO transport + Claude Desktop config | 1 | S | All local usage |
| F2 | IAM compartment tree traversal | 1 | M | F3, F4, F5 |
| F3 | Monitoring queries (metrics + logs) | 2 | M | — |
| F4 | Full CRUD with confirmation/rollback | 3 | L | — |
| F5 | Multi-region query routing | 4 | M | F4 |
| F6 | Extensions SDK for custom tools | 5 | L | F4, F5 |
| F7 | OCI Console SSO integration | 5 | L | F1 |

**Effort:** S = days, M = 1–2 weeks, L = 3–4 weeks

---

## Phase 1 — Foundation `(target: current + 2 weeks)`

> Makes the server usable from Claude Desktop and aware of the full compartment hierarchy.

### F1 · STDIO Transport + Claude Desktop Config

**Why first:** HTTP is wrong for local AI agent use. Claude Desktop, Cursor, and VS Code all expect STDIO. This is the packaging step that turns the server from a Docker container into a one-line install.

Tasks:
- [x] Add `--transport stdio` launch mode to `mcp_server.py` entry point
- [x] Split FastAPI app mount from MCP instance (MCP can run standalone without FastAPI for STDIO)
- [x] Generate `claude_desktop_config.json` snippet on first run (`--print-config`)
- [x] Add `pyproject.toml` with `[project.scripts]` entry point: `oci-mcp = mcp_server:cli`
- [ ] Publish to PyPI as `oci-context-mcp`
- [x] Write install instructions: `uvx oci-context-mcp` (zero-install via uvx)

**Deliverable:** `uvx oci-context-mcp` works in Claude Desktop claude_desktop_config.json

---

### F2 · IAM Compartment Tree Traversal

**Why first:** Every query is currently scoped to a single flat compartment. Real OCI tenancies have 3–6 levels of compartment nesting. Without tree traversal, the server is blind to most resources.

Tasks:
- [x] Add `build_compartment_tree(root_id)` helper — recursive `list_compartments` with depth limit (default 5)
- [x] Cache tree in-memory with 5-minute TTL (compartments rarely change)
- [x] Add `compartment_scope` param: `single` (current) | `recursive` | `tenancy`
- [x] Update key tools (compute, buckets, VCNs, databases) to accept `compartment_scope`
- [x] Add `get_compartment_tree` tool — returns JSON tree of all compartments
- [x] Add `resolve_compartment_by_name(name)` helper for human-friendly queries
- [ ] Update remaining tools (subnets, security_lists, block_volumes, etc.) to accept `compartment_scope`

**Deliverable:** `list_compute_instances(compartment_scope="recursive")` returns instances across all child compartments

---

## Phase 2 — Observability `(target: +2 weeks)`

> Highest operational value. Ops teams ask AI assistants about metrics and logs more than any other OCI service.

### F3 · Monitoring Queries (Metrics + Logs)

Tasks:

**Metrics (OCI Monitoring API):**
- [x] Add `monitoring_client = oci.monitoring.MonitoringClient`
- [x] `list_metric_namespaces(compartment_id)` — what metrics exist
- [x] `query_metrics(namespace, metric_name, start, end, interval, statistic)` — time-series data
- [x] `list_alarms(compartment_id, lifecycle_state)` — alarm definitions
- [x] `get_alarm_status` — current firing state for all alarms
- [x] `list_alarm_history(alarm_id, hours=24)` — recent state transitions

**Logs (OCI Logging):**
- [x] Add `logging_client = oci.logging.LoggingManagementClient`
- [x] Add `log_search_client = oci.loggingsearch.LogSearchClient`
- [x] `list_log_groups(compartment_id)`
- [x] `list_logs(log_group_id)`
- [x] `search_logs(query, time_start, time_end, limit=100)` — full OCI Logging Search query language
- [ ] Natural language → OCI log query translation helper (use oracle_context_prompt)

**Deliverable:** Agent can answer "show me all 5xx errors in the last hour" and "which alarms fired today"

---

## Phase 3 — Control Plane (CRUD) `(target: +3–4 weeks)`

> Most complex and highest risk. Requires confirmation/rollback framework before any write tools are added.

### F4 · Full CRUD with Confirmation/Rollback

**Safety framework (build first, before any write tools):**
- [ ] `OperationPlan` dataclass: `{action, resource_type, resource_id, payload, reversible, rollback_fn}`
- [ ] `require_confirmation(plan)` — returns plan summary, blocks until `confirm=True` passed
- [ ] `StateSnapshot.capture(resource_id)` — fetches current state before mutation
- [ ] `RollbackRegistry` — in-memory log of reversible operations with 30-minute expiry
- [ ] `rollback_last(n=1)` tool — reverses last N operations from registry
- [ ] Dry-run mode: `--dry-run` flag returns plan without executing

**Write tools (add after safety framework complete):**

| Tool | OCI SDK Call | Reversible |
|------|-------------|-----------|
| `create_instance` | `compute_client.launch_instance` | Yes (terminate) |
| `terminate_instance` | `compute_client.terminate_instance` | No |
| `create_bucket` | `os_client.create_bucket` | Yes (delete) |
| `delete_bucket` | `os_client.delete_bucket` | No |
| `upload_object` | `os_client.put_object` | Yes (delete) |
| `delete_object` | `os_client.delete_object` | No |
| `create_vcn` | `network_client.create_vcn` | Yes (delete) |
| `update_security_list` | `network_client.update_security_list` | Yes (snapshot+restore) |
| `create_adb` | `database_client.create_autonomous_database` | Yes (terminate) |
| `scale_instance` | `compute_client.update_instance` | Yes (revert shape) |

Tasks:
- [ ] Build `OperationPlan` + `RollbackRegistry` framework
- [ ] Add dry-run middleware to all write tools
- [ ] Implement 10 write tools above
- [ ] Add `list_pending_operations` tool (shows unrolled-back ops)
- [ ] Add `rollback_operation(operation_id)` tool

**Deliverable:** Agent can create/delete resources with confirmation gate and 30-minute rollback window

---

## Phase 4 — Multi-Region `(target: +1–2 weeks)`

> Depends on CRUD being stable. Required for enterprise tenancies spanning multiple regions.

### F5 · Multi-Region Query Routing

Tasks:
- [ ] `RegionalClientPool` — lazy-initialises OCI SDK clients per region on first request
- [ ] `list_available_regions()` — from `identity_client.list_regions()` at startup
- [ ] Add `region` param to all read tools (default: `$OCI_REGION`)
- [ ] Add `region="all"` wildcard — fans out query to all subscribed regions, merges results
- [ ] `cross_region_search(query)` — runs `search_resources` in all regions in parallel (asyncio.gather)
- [ ] Add region tag to all response objects: `{"region": "us-phoenix-1", ...}`
- [ ] Rate-limit cross-region fan-out to avoid OCI API throttling (semaphore, max 5 concurrent)

**Deliverable:** `list_compute_instances(region="all")` returns unified inventory across every region

---

## Phase 5 — Ecosystem `(target: +4–6 weeks)`

> Enables third-party extension and enterprise SSO. Highest complexity, lowest urgency.

### F6 · Extensions SDK for Custom Tools

Tasks:
- [ ] Define `OCIToolPlugin` base class with `name`, `description`, `schema`, `execute(ctx, **kwargs)` interface
- [ ] `PluginLoader` — scans `~/.oci-mcp/plugins/` and `./plugins/` at startup
- [ ] Hot-reload support — `inotify`/`FSEvents` watcher re-registers tools without server restart
- [ ] Plugin manifest: `plugin.yaml` with name, version, author, required_permissions
- [ ] Permission sandboxing: plugins declare which OCI clients they need; server grants only those
- [ ] Publish `oci-mcp-sdk` as separate PyPI package (just the base classes + helpers)
- [ ] Example plugins: `cost-anomaly-detector`, `compliance-checker`, `network-topology-mapper`

**Deliverable:** `~/.oci-mcp/plugins/my_tool.py` is auto-loaded and appears as an MCP tool

---

### F7 · OCI Console SSO Integration

Tasks:
- [ ] Implement OIDC Authorization Code flow against OCI IAM Identity Domains
- [ ] `GET /auth/login` — redirects to OCI Console OAuth2 endpoint
- [ ] `GET /auth/callback` — exchanges code for token, stores in encrypted session
- [ ] Token refresh via `refresh_token` grant before expiry
- [ ] Map OCI Console session to Instance Principal fallback for tool calls
- [ ] Generate MCP client config with OAuth2 bearer token injection
- [ ] Optional: `--auth-mode browser` flag launches login flow in default browser

**Deliverable:** `oci-mcp --auth-mode browser` opens OCI Console login, no API key needed

---

## Dependency Graph

```
F1 (STDIO)
  └── F7 (SSO)

F2 (Compartment Tree)
  ├── F3 (Monitoring)
  ├── F4 (CRUD)
  │     └── F5 (Multi-region)
  │           └── F6 (Extensions SDK)
  └── F5 (Multi-region)
```

---

## Definition of Done

| Feature | Done When |
|---------|-----------|
| F1 STDIO | `uvx oci-context-mcp` works in Claude Desktop config |
| F2 Compartments | `get_compartment_tree` returns full hierarchy; all tools accept `compartment_scope` |
| F3 Monitoring | `search_logs(query)` and `get_alarm_status` return live OCI data |
| F4 CRUD | 10 write tools pass dry-run, confirmation, and rollback integration tests |
| F5 Multi-region | `region="all"` fan-out works; results tagged with source region |
| F6 Extensions | Plugin in `~/.oci-mcp/plugins/` appears as live MCP tool without restart |
| F7 SSO | Browser login flow produces valid session; no `.oci/config` required |
