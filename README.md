# Oracle OCI Context MCP Server

Production-ready, multi-tenant Model Context Protocol (MCP) server for Oracle Cloud Infrastructure. Gives AI agents (Claude, Cursor, VS Code, OCI GenAI) secure, natural-language access to any OCI tenancy — 69 tools across 22 services.

Part of the **OkOCI Platform**: MCP Context → Inference Gateway (NVIDiOCI) → Deployment CLI (OkOCI Deploy).

---

## Quickstart — Local (Claude Desktop / Cursor / VS Code)

```bash
uvx oci-context-mcp --print-config
```

Prints the ready-to-paste `mcpServers` block:

```json
{
  "mcpServers": {
    "oci": {
      "command": "uvx",
      "args": ["oci-context-mcp", "--transport", "stdio"],
      "env": {
        "OCI_COMPARTMENT_ID": "ocid1.compartment.oc1...",
        "OCI_REGION": "us-phoenix-1"
      }
    }
  }
}
```

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv`). Uses `~/.oci/config` automatically.

---

## Platform Vision

```
AI Agent (Claude, Cursor, OCI GenAI, LangChain)
    |
    v
MCP Server  [this repo]
    Context layer: reads OCI state across 22 services
    Gives the AI eyes on the infrastructure
    |
    v
NVIDiOCI Inference Gateway  [roadmap]
    Brain layer: NVIDIA NIM + Oracle GenAI routing
    Generates structured deployment plans
    |
    v
OkOCI Deploy CLI  [roadmap]
    Execution layer: provisions OCI resources
    Deploys websites, APIs, containers from AI-generated plans
```

One user account, one cross-tenancy IAM policy, spans all three layers.

---

## ARCSEM Stack

| Layer | Service | Cost |
|-------|---------|------|
| Auth | Instance Principal + cross-tenancy IAM | Free |
| Registry | OCI Container Registry (OCIR) | Free (500 MB) |
| Compute | Container Instance CI.Standard.A1.Flex ARM | Free tier |
| Secrets | OCI Vault | Free tier |
| Edge | OCI API Gateway | Free (1M req/mo) |
| Monitoring | OCI Logging + Metrics + ONS alarms | Free tier |

---

## Tool Inventory — 69 Tools, 22 Services

| Category | Tools | Count |
|----------|-------|-------|
| Health & Tenancy | server_health, get_tenancy_info, list_regions | 3 |
| Compute | list_compute_instances, list_compute_shapes, get_compute_instance | 3 |
| Object Storage | get_object_storage_namespace, list_buckets, list_objects, get_bucket_details | 4 |
| Identity & IAM | list_compartments, list_users, list_groups, list_policies | 4 |
| Compartment Tree | get_compartment_tree, resolve_compartment_by_name | 2 |
| Networking | list_vcns, list_subnets, list_security_lists, list_route_tables | 4 |
| Block & File Storage | list_block_volumes, list_file_systems | 2 |
| Resource Search | search_resources | 1 |
| Database | list_autonomous_databases, list_db_systems | 2 |
| Monitoring & Alarms | list_metric_namespaces, query_metrics, list_alarms, get_alarm_status, list_alarm_history | 5 |
| Logging | list_log_groups, list_logs, search_logs | 3 |
| Usage / Cost | get_usage_summary | 1 |
| Vault & Secrets | list_vaults, list_secrets | 2 |
| NSG & Load Balancers | list_network_security_groups, list_load_balancers | 2 |
| Compute Extended | list_images, list_boot_volumes, list_instance_configurations | 3 |
| Networking Extended | list_internet_gateways, list_nat_gateways, list_service_gateways, list_drgs | 4 |
| OKE & Containers | list_clusters, list_node_pools, list_container_instances, list_container_repos | 4 |
| Functions | list_applications, list_functions, get_function | 3 |
| Events & Notifications | list_event_rules, list_notification_topics, list_notification_subscriptions | 3 |
| DNS | list_dns_zones, list_dns_zone_records, list_steering_policies | 3 |
| Budgets | list_budgets, get_budget | 2 |
| Audit | list_audit_events | 1 |
| API Gateway | list_api_gateways, list_api_deployments | 2 |
| Bastion | list_bastions, list_bastion_sessions | 2 |
| MySQL & NoSQL | list_mysql_db_systems, list_nosql_tables | 2 |
| DevOps | list_devops_projects | 1 |
| Telemetry | get_metrics_summary | 1 |
| **TOTAL** | | **69** |

`*` tools support `compartment_scope`: `single` | `recursive` | `tenancy`

---

## Docker Deployment

```bash
# Build ARM64 image (matches OCI free tier A1.Flex shape)
export OCIR_NAMESPACE=$(oci artifacts container configuration get-namespace \
  --compartment-id $OCI_COMPARTMENT_ID --query 'data.namespace' --raw-output)

./scripts/push_to_ocir.sh

# Deploy infrastructure
cd infra
cp terraform.tfvars.example terraform.tfvars  # fill in your values
terraform init
terraform apply
```

Terraform provisions the full ARCSEM stack in one apply:
- Container Instance (ARM64, private subnet)
- API Gateway (public HTTPS endpoint)
- NSG (port 8000 from API Gateway subnet only)
- Log Group + Monitoring alarm

---

## Multi-Tenant SaaS (Roadmap)

The server is being extended to a multi-tenant hosted platform. Users connect their OCI tenancy via one cross-tenancy IAM policy — no API keys are ever stored.

**Onboarding flow (coming):**

```
1. User registers at okoci.dev (email + tenancy OCID)
2. System generates their tenant ID
3. User runs one IAM policy in their tenancy:
     Allow dynamic-group <okoci-dg-ocid> to read all-resources in tenancy
4. System probes and confirms access
5. User receives API key for the MCP endpoint
```

See `docs/Main/ARCHITECTURE.md` for the full SaaS architecture.

---

## Connecting OCI Generative AI Agent

1. Push image to OCIR and run `terraform apply`
2. In OCI Console: Generative AI > Agents > Create agent
3. Add tool: Custom > Model Context Protocol
4. Set Remote MCP Server URL to `terraform output mcp_server_url`
5. Authentication: Instance Principal

---

## Authentication

**Instance Principal** (OCI hosted — recommended):
Automatic, keyless. The Container Instance assumes the dynamic group role.

**Config file** (local development):
Falls back to `~/.oci/config` automatically.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OCI_COMPARTMENT_ID` | Yes | — | Compartment OCID for queries |
| `OCI_REGION` | No | `us-phoenix-1` | OCI region |
| `LOG_LEVEL` | No | `INFO` | Loguru log level |
| `OCI_MCP_TELEMETRY` | No | `local` | `local` or `off` |

---

## Roadmap

### Current — v2.5 (Single-Tenant)
- [x] 69 tools across 22 OCI services
- [x] STDIO transport — Claude Desktop, Cursor, VS Code
- [x] HTTP transport — Docker, Container Instance
- [x] ARCSEM stack — full Terraform IaC
- [x] ARM64 Docker image — OCI free tier compatible
- [x] API Gateway — public HTTPS, private CI

### v3.0 — Multi-Tenant SaaS
- [ ] Per-request OCIAuthManager (cross-tenancy signer)
- [ ] Tenant registry (OCI Autonomous DB)
- [ ] API key service (issuance, rotation, revocation)
- [ ] Landing page + sign-up flow
- [ ] JWT auth policy on API Gateway
- [ ] Per-tenant rate limiting
- [ ] Usage dashboard

### v3.5 — NVIDiOCI Inference Integration
- [ ] MCP tool: `route_inference()` — call NVIDiOCI gateway from MCP context
- [ ] OCI state snapshot passed to inference request
- [ ] NVIDIA NIM + Oracle GenAI routing
- [ ] Structured deploy plan output format

### v4.0 — OkOCI Deploy CLI
- [ ] CLI consumes NVIDiOCI deploy plan JSON
- [ ] Provisions OCI resources via Terraform + OCI SDK
- [ ] Website, API, container deployment targets
- [ ] Rollback support
- [ ] Plugs into existing ARCSEM stack

### F4 — Write Operations (parallel track)
- [ ] Write tools with confirmation + rollback
- [ ] start/stop compute instances
- [ ] create/delete buckets
- [ ] scale OKE node pools

---

## Security

- Instance Principal auth — no credentials stored or transmitted
- Cross-tenancy IAM — user controls access, revokes by deleting one policy
- Private subnet — Container Instance has no public IP
- API Gateway — single public ingress, CORS locked to gateway hostname
- NSG — port 8000 accessible only from API Gateway subnet
- OCI Vault — secrets never in environment variables in production
- Telemetry — local JSONL only, no data leaves the host

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). For questions: denn.stewartjr@gmail.com

---

## License

MIT
