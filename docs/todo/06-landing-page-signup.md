# Landing Page + Sign-up Flow

**Status:** pending
**Owner:** unassigned
**Blocked by:** 04-tenant-registry-db.md, 05-multi-tenant-jwt-gateway.md
**Priority:** high

## Context
The oracle-oci-hub repo needs a functional landing page that:
- Explains the OkOCI Platform (MCP + NVIDiOCI + Deploy)
- Handles user sign-up for the multi-tenant MCP SaaS
- Walks users through the cross-tenancy IAM policy setup
- Issues API keys after tenancy verification

This is the user-facing entry point for the platform.

## Acceptance Criteria
- [ ] Static marketing page: platform overview, tool count, ARCSEM stack
- [ ] Sign-up form: email + OCI tenancy OCID + region
- [ ] Onboarding wizard step 1: shows user the exact IAM policy command
- [ ] Onboarding wizard step 2: "Verify Access" button triggers probe API call
- [ ] On successful probe: issues API key, shows MCP endpoint URL
- [ ] Dashboard: usage stats, key rotation, revoke access
- [ ] Deployed to OCI (Compute free tier) or GitHub Pages (static sections only)
- [ ] HTTPS with OCI API Gateway or Cloudflare

## Technical Notes
Stack recommendation: Next.js 14 (App Router) + Tailwind CSS
- API routes handle sign-up backend logic
- Can be hosted on OCI Compute free tier (VM.Standard.A1.Flex) same as MCP
- Alternative: static export for marketing pages + separate API service

The IAM policy command to show users:
```bash
oci iam policy create \
  --compartment-id <THEIR_TENANCY_OCID> \
  --name "okoci-mcp-read-access" \
  --statements '["Allow dynamic-group <OKOCI_DG_OCID> to read all-resources in tenancy"]' \
  --description "OkOCI MCP read access — revoke by deleting this policy"
```

## References
- Platform vision session 2026-02-23
- docs/Main/ARCHITECTURE.md
- oracle-oci-hub repo (separate repository)
