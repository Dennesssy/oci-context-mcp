# Oracle Context MCP Server

Production-ready OCI MCP (Model Context Protocol) server with **38 tools** across 12 OCI services. Gives AI agents (Claude Desktop, Cursor, VS Code, OCI GenAI) secure, natural-language access to your Oracle Cloud Infrastructure.

## One-Line Install (Claude Desktop / Cursor)

```bash
uvx oci-context-mcp --print-config
```

This prints the ready-to-paste `mcpServers` block for your `claude_desktop_config.json`:

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

Paste it into:
- **Claude Desktop**: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
- **Cursor**: `.cursor/mcp.json` in your project root
- **VS Code**: `.vscode/mcp.json`

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) (`brew install uv` on macOS). Your `~/.oci/config` is used automatically for auth when not running on OCI.

---

## Features

- **28 Production Tools** across:
  - Compute (list instances, shapes, details)
  - Object Storage (namespaces, buckets, objects)
  - Networking (VCNs, subnets, security lists, route tables)
  - Identity & IAM (compartments, users, groups, policies)
  - Database (Autonomous DB, DB Systems)
  - Block & File Storage (volumes, file systems)
  - Resource Search (free-text search)
  - Vault & Secrets
  - Monitoring (usage/cost)
  - Health & Tenancy info

- **Deep OCI IAM Integration**:
  - Instance Principal (keyless, recommended for OCI hosting)
  - Config file fallback for local development
  - IAM policy validation hooks

- **Production Ready**:
  - FastAPI + MCP protocol
  - Async/await throughout
  - Comprehensive error handling
  - Logging with rotation
  - CORS-enabled
  - Environment-driven config

## Quick Start

### Local Development

```bash
# Set environment
export OCI_COMPARTMENT_ID=ocid1.compartment.oc1...
export OCI_REGION=us-phoenix-1
export LOG_LEVEL=INFO

# Install dependencies
pip install -r requirements.txt

# Run server
python mcp_server.py
```

Server runs on http://localhost:8000
- Health check: http://localhost:8000/health
- MCP endpoint: http://localhost:8000/mcp

### Docker Deployment

```bash
# Build image
docker build -t oci-context-mcp:latest .

# Push to OCIR
docker tag oci-context-mcp:latest ocir.us-phoenix-1.ocir.io/YOUR_TENANCY/oci-context-mcp:latest
docker push ocir.us-phoenix-1.ocir.io/YOUR_TENANCY/oci-context-mcp:latest
```

### OCI Infrastructure Deployment

```bash
cd infra

# Initialize Terraform
terraform init

# Plan deployment
terraform plan -var-file=terraform.tfvars

# Deploy
terraform apply -var-file=terraform.tfvars
```

## Tool Inventory

| Category | Tools | Count |
|----------|-------|-------|
| Health & Tenancy | server_health, get_tenancy_info, list_regions | 3 |
| Compute | list_compute_instances*, list_compute_shapes, get_compute_instance | 3 |
| Object Storage | get_object_storage_namespace, list_buckets*, list_objects, get_bucket_details | 4 |
| Identity & IAM | list_compartments, list_users, list_groups, list_policies | 4 |
| Compartment Tree | get_compartment_tree, resolve_compartment_by_name | 2 |
| Networking | list_vcns*, list_subnets, list_security_lists, list_route_tables | 4 |
| Block & File Storage | list_block_volumes, list_file_systems | 2 |
| Resource Search | search_resources | 1 |
| Database | list_autonomous_databases*, list_db_systems* | 2 |
| Monitoring & Alarms | list_metric_namespaces*, query_metrics, list_alarms*, get_alarm_status*, list_alarm_history | 5 |
| Logging | list_log_groups*, list_logs, search_logs | 3 |
| Usage / Cost | get_usage_summary | 1 |
| Vault & Secrets | list_vaults, list_secrets | 2 |
| NSG & Load Balancers | list_network_security_groups, list_load_balancers | 2 |
| **TOTAL** | | **37** |

`*` supports `compartment_scope`: `single` (default) \| `recursive` \| `tenancy`

## Connecting OCI Generative AI Agent

1. Push Docker image to OCIR
2. Deploy with Terraform (creates Container Instance)
3. In OCI Console, go to Generative AI > Agents
4. Create agent
5. Add tool > Custom > Model Context Protocol
6. Configure:
   - **Remote MCP Server**: `http://<container-instance-ip>:8000/mcp`
   - **Authentication**: Instance Principal
   - **Audience**: (leave default or use tenancy ID)
7. Connect and test

## Architecture

```
AI Agent (OCI GenAI, LangChain, etc.)
    ↓
OCI API Gateway (optional WAF/rate limiting)
    ↓
MCP Protocol Endpoint (/mcp)
    ↓
Oracle Context Server (FastAPI)
    ↓
OCI AuthManager (Instance Principal)
    ↓
OCI SDK Clients (Compute, Storage, Identity, etc.)
```

## Environment Variables

```env
OCI_COMPARTMENT_ID  # Compartment OCID for queries
OCI_REGION          # OCI region (default: us-phoenix-1)
LOG_LEVEL           # Logging level (default: INFO)
```

## API References

- [OCI SDK for Python](https://docs.oracle.com/en-us/iaas/tools/python/latest/)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [OCI Generative AI Service](https://docs.oracle.com/en-us/iaas/Content/generative-ai/home.htm)

## Security

- Instance Principal auth (no hardcoded credentials)
- IAM policies enforce least-privilege
- CORS restricted in production (update allow_origins)
- All secrets from environment variables
- Logging does not log sensitive data (set verbose: 0 for credentials)

## Troubleshooting

**Instance Principal not available locally?**
- Use `~/.oci/config` file
- Set `OCI_CONFIG_FILE` env var if needed

**Compartment ID errors?**
- Verify `OCI_COMPARTMENT_ID` env var is set
- Check IAM policies for dynamic group

**Container Instance not responding?**
- Check `terraform output mcp_server_url`
- Verify subnet has egress rules
- Check Container Instance logs in OCI Console

## License

MIT

## Telemetry

By default the server writes one JSON line per tool call to `oci_mcp_metrics.jsonl` on the host. **No data leaves your machine.**

Each event records: `tool`, `ok`, `ms`, `version`, and a one-way hash of your region (not the OCID). No compartment IDs, no credentials, no query content.

```bash
# Ask the agent directly
get_metrics_summary()

# Or inspect the raw file
cat oci_mcp_metrics.jsonl | jq -s 'group_by(.tool) | map({tool: .[0].tool, calls: length})'
```

To disable: `OCI_MCP_TELEMETRY=off` in your environment or `.env` file.

## Coverage vs OCI CLI

The OCI CLI exposes ~130 service groups. This server covers **~13%** (the highest-traffic read paths).

| Covered | Gap (planned in BUILD_PLAN.md) |
|---------|-------------------------------|
| Compute, Object Storage, Block/File Storage | `monitoring` (metrics, alarms) |
| Identity, IAM, Vault, Secrets | `logging` (log groups, log search) |
| Networking (VCN, subnets, SGs, routes, NSG) | `ce` (OKE clusters), `fn` (Functions) |
| Database (ADB, DB Systems) | `events`, `ons` (Notifications) |
| Load Balancers, Resource Search | `audit`, `dns`, `budgets`, `bastion`, `kms` |
| Usage / Cost summary | `api-gateway`, `container-instances`, `generative-ai` |

See [BUILD_PLAN.md](BUILD_PLAN.md) for the full F1–F7 roadmap toward parity with Azure MCP.

## Roadmap

- [x] F1: STDIO transport + `uvx oci-context-mcp` packaging (v2.1)
- [x] F2: IAM compartment tree traversal (recursive multi-compartment) (v2.2)
- [x] F3: Monitoring (metrics, alarms, log search) (v2.3)
- [ ] F4: Write operations with confirmation + rollback
- [ ] F5: Multi-region fan-out
- [ ] F6: Plugin SDK for custom tools
- [ ] F7: OCI Console SSO / browser auth
