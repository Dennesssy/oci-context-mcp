# Refactor OCIAuthManager for multi-tenancy

**Status:** pending
**Owner:** unassigned
**Blocked by:** 04-tenant-registry-db.md
**Priority:** critical

## Context
`auth_manager = OCIAuthManager()` on line 211 of `mcp_server.py` is a
module-level singleton. It initialises one signer against one OCI tenancy at
server startup. This is a hard blocker for multi-tenant SaaS.

Multi-tenancy requires a per-request auth context scoped to the requesting
user's tenancy OCID and region, using a cross-tenancy signer derived from the
server's own Instance Principal.

## Acceptance Criteria
- [ ] `OCIAuthManager` accepts `tenant_ocid` and `region` constructor arguments
- [ ] Cross-tenancy signer initialised per-request using `oci.auth.signers.InstancePrincipalsSecurityTokenSigner` with target tenancy OCID
- [ ] Per-tenant `OCIAuthManager` instances cached with a 10-minute TTL (keyed on `tenant_ocid + region`)
- [ ] Cache invalidation on 401/403 from OCI SDK (force re-auth)
- [ ] All 69 tool functions receive auth context via FastAPI `Depends()` — no global `auth_manager` reference remains
- [ ] Existing single-tenant mode (env var `MULTI_TENANT=false`) preserves current behaviour
- [ ] Unit tests in `tests/` cover single-tenant and multi-tenant auth paths

## Technical Notes
Cross-tenancy signer pattern:
```python
signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
# To call into another tenancy, pass target tenancy OCID in the API call
# OCI SDK supports cross-tenancy by setting the tenancy context on individual clients
```

FastAPI dependency pattern:
```python
async def get_tenant_auth(tenant_id: str = Depends(extract_tenant_from_jwt)) -> OCIAuthManager:
    return await auth_cache.get_or_create(tenant_id)
```

Every tool function currently does:
```python
auth_manager.compute_client.list_instances(...)
```
Will become:
```python
async def list_compute_instances(auth: OCIAuthManager = Depends(get_tenant_auth), ...):
    auth.compute_client.list_instances(...)
```

This is a large but mechanical refactor — all 69 tools follow the same pattern.

## References
- ARCSEM audit + platform vision session 2026-02-23
- `mcp_server.py` lines 157-211 (OCIAuthManager class)
- `mcp_server.py` line 211 (singleton instantiation — remove this)
- Blocked by: tenant registry must exist before per-request lookup works
