# OkOCI Deploy CLI

**Status:** pending
**Owner:** unassigned
**Blocked by:** 07-nvidioci-inference-integration.md
**Priority:** high

## Context
OkOCI Deploy is the execution layer of the OkOCI Platform. It consumes the
structured deploy plan JSON produced by NVIDiOCI and provisions OCI resources
using Terraform + OCI SDK. It will live in a separate repository.

This task tracks the design and initial implementation.

## Acceptance Criteria
- [ ] New repository: `okoci-deploy` (separate from this repo)
- [ ] CLI entry point: `okoci deploy <plan.json>`
- [ ] Parses NVIDiOCI deploy plan JSON schema
- [ ] Supports deployment targets: static website, API server, container, OKE workload
- [ ] Generates and applies Terraform from plan (or calls OCI SDK directly)
- [ ] Dry-run mode: `okoci deploy --dry-run <plan.json>` prints Terraform plan without applying
- [ ] Rollback: `okoci rollback <deployment-id>`
- [ ] Uses cross-tenancy signer from tenant config (same auth model as MCP server)
- [ ] Writes deployment events to tenant registry usage_events table

## Technical Notes
Deploy plan JSON schema (proposed):
```json
{
  "version": "1.0",
  "tenant_ocid": "ocid1.tenancy.oc1...",
  "region": "us-phoenix-1",
  "deployment_type": "container",
  "resources": [
    {
      "type": "container_instance",
      "shape": "CI.Standard.A1.Flex",
      "ocpus": 1,
      "memory_gb": 6,
      "image": "phx.ocir.io/<ns>/<repo>:<tag>",
      "subnet_id": "ocid1.subnet.oc1..."
    }
  ],
  "generated_by": "nvidioci-gateway",
  "model": "meta/llama-3.1-70b-instruct",
  "confidence": 0.92
}
```

The CLI should use `click` (already a dependency in this repo) and `python-oci-sdk`.

## References
- Platform vision session 2026-02-23
- docs/Main/ARCHITECTURE.md
- NVIDiOCI deploy plan output format to be confirmed in task 07
