# F4: Write Operations with Confirmation + Rollback

**Status:** pending
**Owner:** unassigned
**Blocked by:** 03-multi-tenant-auth-manager.md
**Priority:** medium

## Context
The MCP server is currently read-only. F4 adds write tools (start/stop compute,
create/delete storage, scale OKE) with a confirmation gate and rollback support.
Required for the OkOCI Deploy integration and for parity with Azure MCP.

## Acceptance Criteria
- [ ] Write tools gated behind `ALLOW_WRITE_TOOLS=true` env var (default: false)
- [ ] Confirmation pattern: tool returns a `confirmation_token` on first call; client must call again with the token to execute
- [ ] Rollback: each write operation records the pre-change state; `undo_last_operation()` tool reverts it
- [ ] Initial write tools:
  - [ ] `start_compute_instance(instance_id)`
  - [ ] `stop_compute_instance(instance_id)`
  - [ ] `create_bucket(name, compartment_id)`
  - [ ] `delete_bucket(name)` — only if empty
  - [ ] `scale_oke_node_pool(cluster_id, pool_id, size)`
- [ ] IAM policy additions for write operations documented in `infra/main.tf`
- [ ] Audit log entry written to OCI Audit service on every write

## Technical Notes
Confirmation pattern:
```python
@mcp.tool()
async def stop_compute_instance(instance_id: str, confirm: str = None):
    if confirm != expected_token:
        token = generate_confirmation_token(instance_id, "stop")
        return f"Confirm by calling again with confirm='{token}'"
    # execute stop
```

IAM additions needed (currently only `read` verbs):
```
Allow dynamic-group oci-context-mcp-dg to manage instances in compartment id ...
Allow dynamic-group oci-context-mcp-dg to manage buckets in compartment id ...
Allow dynamic-group oci-context-mcp-dg to manage cluster-node-pools in compartment id ...
```

## References
- BUILD_PLAN.md F4 section
- README.md Roadmap section
- Blocked until multi-tenant auth is in place (write ops must be tenant-scoped)
