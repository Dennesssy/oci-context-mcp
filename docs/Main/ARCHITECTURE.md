# OkOCI Platform Architecture

**DO NOT MODIFY** without creating a task in `docs/todo/` and explicit confirmation.

---

## Platform Overview

```
User / AI Agent
      |
      v
MCP Server  (oracle-oci-context-mcp-server)
  - 69 read tools across 22 OCI services
  - Context layer: gives the AI eyes on OCI infrastructure
  - Multi-tenant via cross-tenancy IAM (v3.0)
      |
      | OCI state snapshot (JSON)
      v
NVIDiOCI Inference Gateway
  - NVIDIA NIM + Oracle GenAI + Groq routing
  - Brain layer: reasons about OCI context, generates plans
  - OCI Functions + API Gateway
      |
      | structured deploy plan (JSON)
      v
OkOCI Deploy CLI  (okoci-deploy repo)
  - Execution layer: provisions OCI resources from AI-generated plan
  - Terraform + OCI SDK
  - Targets: website, API, container, OKE workload
```

---

## Multi-Tenant Credential Model

### Cross-Tenancy IAM (chosen approach)

Users add one IAM policy in their OCI tenancy. No credentials are stored
on the OkOCI platform — ever.

```
OkOCI Platform (its own OCI tenancy)
  Instance Principal -> Dynamic Group: okoci-platform-dg

User's OCI Tenancy
  IAM Policy: "Allow dynamic-group <okoci-platform-dg-ocid>
               to read all-resources in tenancy"

Runtime:
  MCP server uses Instance Principal signer from platform tenancy
  + target tenancy OCID from JWT claim
  = cross-tenancy API calls to user's resources
```

User revokes access by deleting their IAM policy. Zero credential liability
on the platform side.

### What is NOT used

- OCI API private keys stored on platform servers
- User API keys submitted via web form
- OAuth tokens stored in database
- Long-lived credentials of any kind

---

## Request Flow (Multi-Tenant)

```
AI Agent
  |
  | Bearer JWT (contains: tenant_id, region, scope)
  v
OCI API Gateway
  - Validates JWT (JWKS endpoint: auth.okoci.dev)
  - Extracts tenant_id, region claims
  - Forwards as X-Tenant-Id, X-Tenant-Region headers
  - Rate limits per tenant_id
  |
  | internal HTTPS (private subnet)
  v
MCP Server (Container Instance, A1.Flex, private subnet)
  - Middleware reads X-Tenant-Id header
  - Looks up tenant config in registry (tenancy_ocid, region)
  - Gets or creates cross-tenancy OCIAuthManager (10-min TTL cache)
  - Executes tool call against user's tenancy
  - Writes usage event to registry
  |
  v
User's OCI Tenancy resources
```

---

## ARCSEM Infrastructure Stack

All six layers deploy from `infra/main.tf` via `terraform apply`.

| Layer | Resource | Shape / Tier |
|-------|---------|-------------|
| Auth | Instance Principal + Dynamic Group + IAM Policy | Free |
| Registry | OCIR `oci-context-mcp` repo | Free (500 MB) |
| Compute | Container Instance CI.Standard.A1.Flex | Free tier (1 OCPU / 6 GB) |
| Secrets | OCI Vault + secret: `nvidia-api-key`, `db-connection` | Free tier |
| Edge | API Gateway PUBLIC + Deployment | Free (1M req/mo) |
| Monitoring | Log Group + Custom Log + ONS Topic + Alarm (5XX) | Free tier |

Private subnet: Container Instance has no public IP. API Gateway is the
only internet-facing component.

NSG rule: port 8000 ingress from API Gateway subnet CIDR only.

---

## Repository Map

| Repo | Layer | Status |
|------|-------|--------|
| oracle-oci-context-mcp-server | MCP Context | Active |
| oracle-oci-hub | Landing page + docs hub | Planned |
| NVIDiOCI (nvidia-oci-gateway) | Inference Gateway | 20% |
| okoci-deploy | Deploy CLI | Not started |

---

## Dependency Graph (Task Order)

```
04-tenant-registry-db
    |--- 03-multi-tenant-auth-manager
    |         |--- 09-mcp-write-tools-f4
    |         |--- 07-nvidioci-inference-integration
    |                   |--- 08-okoci-deploy-cli
    |
    |--- 05-multi-tenant-jwt-gateway
              |--- 02-cors-lockdown
              |--- 06-landing-page-signup

01-pyproject-fixes    (no deps, do first)
```

---

## Design Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-02-23 | ARM64 Docker image (CI.Standard.A1.Flex) | OCI free tier — zero compute cost |
| 2026-02-23 | Private subnet, no public IP on Container Instance | API Gateway is sole ingress |
| 2026-02-23 | Cross-tenancy IAM (not API key storage) | Zero credential liability on platform |
| 2026-02-23 | Multi-tenant SaaS over single-tenant | Scales to hosted platform, Oracle C-suite positioning |
| 2026-02-23 | `OCIAuthManager` per-request in v3.0 | Required for multi-tenancy — no new singleton dependencies |
| 2026-02-23 | OCI Autonomous DB for tenant registry | Free tier, aligns with Oracle ecosystem |
| 2026-02-23 | Next.js for landing page | SSR + API routes, OCI Compute hostable |
