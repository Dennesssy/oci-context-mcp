# NVIDiOCI Inference Gateway Integration

**Status:** pending
**Owner:** unassigned
**Blocked by:** 03-multi-tenant-auth-manager.md
**Priority:** high

## Context
NVIDiOCI (NVIDIA NIM + OCI Functions gateway) is the inference layer of the
OkOCI Platform. The MCP server needs a tool that passes OCI context to the
inference gateway and returns a structured response (e.g., a deployment plan).

This integration enables the full MCP -> Inference -> Deploy pipeline.

## Acceptance Criteria
- [ ] NVIDiOCI gateway deployed (Phase 1-5 of NVIDiOCI project complete)
- [ ] New MCP tool: `route_inference(prompt, context_scope)` added to `mcp_server.py`
- [ ] Tool collects OCI state snapshot (compute, networking, costs) and attaches to inference request
- [ ] Response from NVIDiOCI returned as structured JSON with `deploy_plan` field
- [ ] NVIDIA API key stored in OCI Vault, retrieved at runtime — never in env vars
- [ ] Integration test: end-to-end from Claude Desktop tool call to NVIDiOCI response
- [ ] NVIDiOCI endpoint URL stored in tenant config (each tenant may point to different region)

## Technical Notes
NVIDiOCI gateway endpoint (after Phase 4 deploy):
```
POST https://<gateway>.apigateway.<region>.oci.customer-oci.com/v1/chat/completions
```

OCI state snapshot to attach:
```python
snapshot = {
  "compute_instances": await list_compute_instances(...),
  "vcns": await list_vcns(...),
  "costs": await get_usage_summary(...),
  "region": tenant.region,
  "tenancy_ocid": tenant.tenancy_ocid,
}
```

NVIDIA API key — fix gateway_spec.json NVAPI_PLACEHOLDER:
```hcl
# In NVIDiOCI Terraform
resource "oci_vault_secret" "nvidia_api_key" {
  compartment_id = var.compartment_id
  vault_id       = var.vault_id
  key_id         = var.vault_key_id
  secret_name    = "nvidia-api-key"
  secret_content {
    content_type = "BASE64"
    content      = base64encode(var.nvidia_api_key)
  }
}
```

## References
- NVIDiOCI project: `/Users/denn/Desktop/LLMPlayground/NVIDiOCI/`
- Platform vision session 2026-02-23
- Blocked until NVIDiOCI Phase 1-5 complete AND multi-tenant auth ready
