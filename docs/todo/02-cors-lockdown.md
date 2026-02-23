# Lock CORS to API Gateway hostname

**Status:** pending
**Owner:** unassigned
**Blocked by:** 05-multi-tenant-jwt-gateway.md
**Priority:** medium

## Context
`mcp_server.py` line 215 sets `allow_origins=["*"]`. This is acceptable while
the Container Instance is on a private subnet with no public IP, but must be
tightened before multi-tenant launch.

The API Gateway Terraform resource already sets CORS correctly at the gateway
layer. This task locks it at the application layer too.

## Acceptance Criteria
- [ ] `allow_origins` reads from `ALLOWED_ORIGINS` environment variable
- [ ] Default value is `["*"]` only when `ENV=development`
- [ ] Terraform `infra/main.tf` passes the API Gateway hostname as `ALLOWED_ORIGINS` to Container Instance env vars
- [ ] Local development still works with `ENV=development` in `.env`

## Technical Notes
File: `mcp_server.py` line 215
The API Gateway hostname is a Terraform computed output (`oci_apigateway_gateway.mcp_gateway.hostname`).
Pass it as an environment variable in the `containers.environment_variables` block in `main.tf`.

## References
- ARCSEM audit 2026-02-23
- `infra/main.tf` — `oci_apigateway_deployment.mcp_deployment` already has CORS locked at GW layer
