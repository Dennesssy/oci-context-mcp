# JWT Auth Policy on API Gateway + Per-Tenant Rate Limiting

**Status:** pending
**Owner:** unassigned
**Blocked by:** 04-tenant-registry-db.md
**Priority:** critical

## Context
The API Gateway currently routes all traffic without authentication. Multi-tenant
SaaS requires every request to carry a JWT containing the tenant_id claim.
The API Gateway validates the JWT and the MCP server reads the claim to
select the correct cross-tenancy signer.

## Acceptance Criteria
- [ ] OCI API Gateway deployment updated with `authentication` request policy (JWT)
- [ ] JWT issuer configured to match the auth service endpoint
- [ ] `tenant_id` and `region` claims extracted and forwarded to backend as headers
- [ ] Per-tenant rate limiting policy applied at API Gateway (default: 100 req/min free tier)
- [ ] 401 response returned for missing or invalid JWT
- [ ] Auth service endpoint that issues JWTs added to `infra/main.tf` or documented separately
- [ ] MCP server middleware reads `X-Tenant-Id` and `X-Tenant-Region` forwarded headers

## Technical Notes
OCI API Gateway JWT auth policy:
```hcl
request_policies {
  authentication {
    type                        = "JWT_AUTHENTICATION"
    token_header                = "Authorization"
    token_auth_scheme           = "Bearer"
    issuers                     = ["https://auth.okoci.dev"]
    audiences                   = ["oci-mcp-server"]
    public_keys {
      type = "REMOTE_JWKS"
      uri  = "https://auth.okoci.dev/.well-known/jwks.json"
    }
  }
}
```

Rate limiting policy:
```hcl
rate_limiting {
  rate_in_requests_per_second = 2  # 100/min = ~2/sec
  rate_key                    = "CLIENT_IP"  # upgrade to JWT claim when available
}
```

## References
- OCI API Gateway JWT auth: https://docs.oracle.com/en-us/iaas/Content/APIGateway/Tasks/apigatewayusingjwttokens.htm
- Blocked by tenant registry (need user lookup for JWT validation)
- Blocks: 02-cors-lockdown.md, 03-multi-tenant-auth-manager.md
