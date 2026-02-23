# Tenant Registry Database

**Status:** pending
**Owner:** unassigned
**Blocked by:** nothing
**Priority:** critical

## Context
Multi-tenant SaaS requires a database to store user accounts, tenancy mappings,
API keys, and usage events. This is the shared backbone used by the auth service,
MCP server, and eventually NVIDiOCI and OkOCI Deploy.

OCI Autonomous Database (free tier, Always Free) is the target — it aligns
with Oracle's ecosystem and is zero-cost.

## Acceptance Criteria
- [ ] Schema designed and reviewed (users, api_keys, tenants, usage_events)
- [ ] Terraform resource for OCI Autonomous DB free tier added to `infra/main.tf`
- [ ] Connection pooling via `cx_Oracle` or `python-oracledb` configured in server
- [ ] Schema migration tooling chosen (Alembic recommended)
- [ ] Initial migration files created in `infra/migrations/`
- [ ] `DB_CONNECTION_STRING` read from OCI Vault (not env var) in production
- [ ] Local dev uses SQLite fallback when `ENV=development`

## Schema

```sql
CREATE TABLE users (
  id            VARCHAR2(36) PRIMARY KEY,  -- UUID
  email         VARCHAR2(255) UNIQUE NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  plan          VARCHAR2(20) DEFAULT 'free',  -- free | pro | enterprise
  status        VARCHAR2(20) DEFAULT 'pending'  -- pending | active | suspended
);

CREATE TABLE tenants (
  id              VARCHAR2(36) PRIMARY KEY,
  user_id         VARCHAR2(36) REFERENCES users(id),
  tenancy_ocid    VARCHAR2(255) UNIQUE NOT NULL,
  region          VARCHAR2(50) NOT NULL,
  verified_at     TIMESTAMP,
  status          VARCHAR2(20) DEFAULT 'pending'
);

CREATE TABLE api_keys (
  id            VARCHAR2(36) PRIMARY KEY,
  user_id       VARCHAR2(36) REFERENCES users(id),
  key_hash      VARCHAR2(255) UNIQUE NOT NULL,  -- bcrypt hash, never store plain
  prefix        VARCHAR2(8) NOT NULL,           -- first 8 chars for display
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  last_used_at  TIMESTAMP,
  expires_at    TIMESTAMP,
  revoked       NUMBER(1) DEFAULT 0
);

CREATE TABLE usage_events (
  id          VARCHAR2(36) PRIMARY KEY,
  tenant_id   VARCHAR2(36) REFERENCES tenants(id),
  tool        VARCHAR2(100) NOT NULL,
  ok          NUMBER(1) NOT NULL,
  ms          NUMBER NOT NULL,
  ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Technical Notes
- OCI Autonomous DB free tier: 1 OCPU, 20 GB storage, Always Free
- Use `python-oracledb` (thin mode — no Oracle Client install needed)
- API keys: store only bcrypt hash + first 8 chars prefix for identification
- Usage events will feed the NVIDiOCI HeatWave analytics layer (future)

## References
- NVIDiOCI HeatWave schema (`/Users/denn/Desktop/LLMPlayground/NVIDiOCI/schema/`) — review for reuse
- Platform vision session 2026-02-23
