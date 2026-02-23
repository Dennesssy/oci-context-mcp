# Oracle Context MCP Server - API Reference

## Overview

The Oracle Context MCP Server is a production-ready Model Context Protocol (MCP) server that provides AI agents with secure, natural-language access to Oracle Cloud Infrastructure (OCI) resources. The server exposes **28 tools** and **1 system prompt** across 10 OCI services.

### What is MCP?

The Model Context Protocol (MCP) is a standardized interface that allows AI models to safely interact with external tools and data sources. The Oracle Context MCP Server implements the MCP protocol over HTTP/FastAPI, enabling AI agents to query and monitor OCI infrastructure without direct API key exposure.

### Key Features

- **28 Production Tools** across Compute, Storage, Networking, Identity, Database, and Vault services
- **Dual Authentication Modes**: Instance Principal (recommended) with API Key fallback
- **Async/Await Architecture**: Built on FastAPI for high-performance concurrent requests
- **Comprehensive Error Handling**: ServiceError-specific exceptions with detailed HTTP status codes
- **Environment-Driven Configuration**: No hardcoded credentials
- **Structured Logging**: Log rotation and configurable verbosity levels
- **CORS-Enabled**: Ready for multi-tenant AI agent deployments

---

## Authentication

### Instance Principal (Recommended)

For OCI hosted deployments (Container Instances, Compute, OKE), the server automatically uses **Instance Principal authentication** for keyless, credential-less access.

```python
# Automatic detection in OCIAuthManager.__init__()
try:
    self.signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    self.using_instance_principal = True
except Exception:
    # Fallback to config file
```

**Setup Requirements:**
1. Create Dynamic Group for compute resources
2. Add IAM policy: `allow dynamic-group <name> to manage all-resources in compartment <compartment-name>`
3. Server logs: `✅ Instance Principal IAM enabled`

### API Key (Fallback)

When running locally or in non-OCI environments, the server falls back to OCI config file authentication.

**Setup:**
1. Create `~/.oci/config` with your API credentials
2. Ensure `OCI_CONFIG_FILE` environment variable points to config path (default: `~/.oci/config`)
3. Server logs: `Instance Principal unavailable: [error]. Using config file.`

### Authentication Header

The server accepts optional `Authorization` header for audit/tracking:

```http
GET /mcp/tools/list_compute_instances HTTP/1.1
Authorization: Bearer <optional-token>
```

---

## Tools Reference Table

| # | Tool Name | Service | Category | Parameters | Returns |
|---|-----------|---------|----------|------------|---------|
| 1 | `server_health` | Health | Health & Tenancy | None | JSON: `{status, server, iam_mode, region, compartment_id}` |
| 2 | `get_tenancy_info` | Identity | Health & Tenancy | None | JSON: `{tenancy_id, region, compartment_id, auth_mode}` |
| 3 | `list_regions` | Identity | Health & Tenancy | None | JSON Array: `[{name, key}, ...]` |
| 4 | `list_compute_instances` | Compute | Compute | `limit: int (opt, default=100)` | JSON Array: `[{id, name, shape, state}, ...]` |
| 5 | `list_compute_shapes` | Compute | Compute | None | JSON Array: `[{shape, ocpus, memory_gb}, ...]` |
| 6 | `get_compute_instance` | Compute | Compute | `instance_id: string (req)` | JSON: `{id, name, shape, state}` |
| 7 | `get_object_storage_namespace` | Object Storage | Object Storage | None | JSON: `{namespace: string}` |
| 8 | `list_buckets` | Object Storage | Object Storage | None | JSON Array: `[{name, created}, ...]` |
| 9 | `list_objects` | Object Storage | Object Storage | `bucket_name: string (req)`, `limit: int (opt, default=100)` | JSON Array: `[{name, size_bytes}, ...]` |
| 10 | `get_bucket_details` | Object Storage | Object Storage | `bucket_name: string (req)` | JSON: `{name, created, public}` |
| 11 | `list_compartments` | Identity | Identity & IAM | `parent_id: string (opt)` | JSON Array: `[{id, name}, ...]` |
| 12 | `list_users` | Identity | Identity & IAM | None | JSON Array: `[{id, name}, ...]` |
| 13 | `list_groups` | Identity | Identity & IAM | None | JSON Array: `[{id, name}, ...]` |
| 14 | `list_policies` | Identity | Identity & IAM | None | JSON Array: `[{id, name, statements}, ...]` |
| 15 | `list_vcns` | Network | Networking | None | JSON Array: `[{id, name, cidr}, ...]` |
| 16 | `list_subnets` | Network | Networking | `vcn_id: string (opt)` | JSON Array: `[{id, name, cidr}, ...]` |
| 17 | `list_security_lists` | Network | Networking | None | JSON Array: `[{id, name}, ...]` |
| 18 | `list_route_tables` | Network | Networking | None | JSON Array: `[{id, name}, ...]` |
| 19 | `list_block_volumes` | Block Storage | Block & File Storage | None | JSON Array: `[{id, name, size_gb}, ...]` |
| 20 | `list_file_systems` | File Storage | Block & File Storage | `availability_domain: string (opt, auto-resolved)` | JSON Array: `[{id, name}, ...]` |
| 21 | `search_resources` | Resource Search | Search | `query: string (req)`, `limit: int (opt, default=50)` | JSON Array: `[{type, id, name}, ...]` |
| 22 | `list_autonomous_databases` | Database | Database | None | JSON Array: `[{id, name, db_name, state}, ...]` |
| 23 | `list_db_systems` | Database | Database | None | JSON Array: `[{id, name}, ...]` |
| 24 | `get_usage_summary` | Metering & Usage | Usage/Cost | `time_start: string (ISO, req)`, `time_end: string (ISO, req)` | JSON Array: `[{service, compartment_name, compartment_id, computed_amount, unit, currency, ...}, ...]` |
| 25 | `list_network_security_groups` | Network | NSGs & Load Balancers | None | JSON Array: `[{id, name, state}, ...]` |
| 26 | `list_load_balancers` | Network | NSGs & Load Balancers | None | JSON Array: `[{id, name, shape, state}, ...]` |
| 27 | `list_vaults` | Vault | Vault & Secrets | None | JSON Array: `[{id, name}, ...]` |
| 28 | `list_secrets` | Vault | Vault & Secrets | None | JSON Array: `[{id, name}, ...]` |

---

## Tool Details

### 1. server_health

**Description:**
Health check endpoint that returns server status, authentication mode, and configuration context.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
{
  "status": "healthy",
  "server": "Oracle Context MCP Server v2.0",
  "iam_mode": "InstancePrincipal" | "Config",
  "region": "us-phoenix-1",
  "compartment_id": "ocid1.compartment.oc1..."
}
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/server_health
```

**Error Handling:**
- Returns `200 OK` on success
- No errors expected (static response)

---

### 2. get_tenancy_info

**Description:**
Retrieves tenancy, authentication mode, and region context information.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
{
  "tenancy_id": "ocid1.tenancy.oc1...",
  "region": "us-phoenix-1",
  "compartment_id": "ocid1.compartment.oc1...",
  "auth_mode": "Instance Principal" | "API Key"
}
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/get_tenancy_info
```

**Error Handling:**
- Returns `200 OK` with error field if exception occurs
- No HTTP exception raised (graceful degradation)

---

### 3. list_regions

**Description:**
Lists all available OCI regions in your tenancy.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "name": "us-phoenix-1",
    "key": "PHX"
  },
  {
    "name": "us-ashburn-1",
    "key": "IAD"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_regions
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure (details in response)

---

### 4. list_compute_instances

**Description:**
Lists Compute instances in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | int | Optional | Maximum results to return (default: 100) |

**Response Format:**
```json
[
  {
    "id": "ocid1.instance.oc1...",
    "name": "my-instance",
    "shape": "VM.Standard.E4.Flex",
    "state": "RUNNING"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/list_compute_instances?limit=50"
```

**Error Handling:**
- `400 Bad Request`: ServiceError from OCI API (insufficient permissions, invalid compartment)
- `500 Internal Server Error`: Generic exception

---

### 5. list_compute_shapes

**Description:**
Lists available Compute shapes in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "shape": "VM.Standard.E4.Flex",
    "ocpus": 1.0,
    "memory_gb": 16.0
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_compute_shapes
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 6. get_compute_instance

**Description:**
Retrieves detailed information about a specific Compute instance.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `instance_id` | string | Required | OCID of the instance (e.g., `ocid1.instance.oc1...`) |

**Response Format:**
```json
{
  "id": "ocid1.instance.oc1...",
  "name": "my-instance",
  "shape": "VM.Standard.E4.Flex",
  "state": "RUNNING"
}
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/get_compute_instance?instance_id=ocid1.instance.oc1..."
```

**Error Handling:**
- `500 Internal Server Error`: Invalid instance ID or OCI API failure

---

### 7. get_object_storage_namespace

**Description:**
Retrieves the tenancy's Object Storage namespace name.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
{
  "namespace": "my-tenancy-namespace"
}
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/get_object_storage_namespace
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 8. list_buckets

**Description:**
Lists all Object Storage buckets in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "name": "my-bucket",
    "created": "2024-01-15T10:30:00"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_buckets
```

**Error Handling:**
- `400 Bad Request`: ServiceError (insufficient permissions)
- `500 Internal Server Error`: Generic exception

---

### 9. list_objects

**Description:**
Lists objects in a specific bucket.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bucket_name` | string | Required | Name of the bucket |
| `limit` | int | Optional | Maximum results to return (default: 100) |

**Response Format:**
```json
[
  {
    "name": "folder/file.txt",
    "size_bytes": 1024
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/list_objects?bucket_name=my-bucket&limit=50"
```

**Error Handling:**
- `400 Bad Request`: ServiceError (bucket not found, insufficient permissions)
- `500 Internal Server Error`: Generic exception

---

### 10. get_bucket_details

**Description:**
Retrieves detailed information about a specific bucket.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `bucket_name` | string | Required | Name of the bucket |

**Response Format:**
```json
{
  "name": "my-bucket",
  "created": "2024-01-15T10:30:00",
  "public": "ObjectRead" | "NoPublicAccess"
}
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/get_bucket_details?bucket_name=my-bucket"
```

**Error Handling:**
- `500 Internal Server Error`: Bucket not found or OCI API failure

---

### 11. list_compartments

**Description:**
Lists compartments in your tenancy hierarchy.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `parent_id` | string | Optional | Parent compartment OCID (defaults to configured compartment) |

**Response Format:**
```json
[
  {
    "id": "ocid1.compartment.oc1...",
    "name": "development"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/list_compartments"
```

**Error Handling:**
- `500 Internal Server Error`: Invalid parent ID or OCI API failure

---

### 12. list_users

**Description:**
Lists IAM users in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.user.oc1...",
    "name": "john.doe@company.com"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_users
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 13. list_groups

**Description:**
Lists IAM groups in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.group.oc1...",
    "name": "developers"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_groups
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 14. list_policies

**Description:**
Lists IAM policies in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.policy.oc1...",
    "name": "dev-policy",
    "statements": [
      "allow group developers to manage instances in compartment development"
    ]
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_policies
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 15. list_vcns

**Description:**
Lists Virtual Cloud Networks in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.vcn.oc1...",
    "name": "my-vcn",
    "cidr": "10.0.0.0/16"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_vcns
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 16. list_subnets

**Description:**
Lists subnets in the configured compartment, optionally filtered by VCN.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `vcn_id` | string | Optional | Filter by VCN OCID |

**Response Format:**
```json
[
  {
    "id": "ocid1.subnet.oc1...",
    "name": "public-subnet",
    "cidr": "10.0.1.0/24"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/list_subnets?vcn_id=ocid1.vcn.oc1..."
```

**Error Handling:**
- `500 Internal Server Error`: Invalid VCN ID or OCI API failure

---

### 17. list_security_lists

**Description:**
Lists security lists in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.securitylist.oc1...",
    "name": "default-security-list"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_security_lists
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 18. list_route_tables

**Description:**
Lists route tables in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.routetable.oc1...",
    "name": "default-route-table"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_route_tables
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 19. list_block_volumes

**Description:**
Lists Block Storage volumes in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.volume.oc1...",
    "name": "data-volume",
    "size_gb": 100
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_block_volumes
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 20. list_file_systems

**Description:**
Lists File Storage file systems. Availability domain is required by OCI API but can be auto-resolved to the first AD if not provided.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `availability_domain` | string | Optional | Availability domain (e.g., `AD-1`, `AD-2`). Auto-resolved if not provided. |

**Response Format:**
```json
[
  {
    "id": "ocid1.filesystem.oc1...",
    "name": "my-file-system"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/list_file_systems"
# OR specify explicit AD:
curl "http://localhost:8000/mcp/tools/list_file_systems?availability_domain=AD-1"
```

**Error Handling:**
- `400 Bad Request`: Availability domain required and could not be auto-resolved
- `500 Internal Server Error`: OCI API failure

---

### 21. search_resources

**Description:**
Free-text search across all OCI resources using the Resource Search service.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `query` | string | Required | Search query (e.g., "database", "tag:environment=prod") |
| `limit` | int | Optional | Maximum results to return (default: 50) |

**Response Format:**
```json
[
  {
    "type": "Instance",
    "id": "ocid1.instance.oc1...",
    "name": "my-instance"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/search_resources?query=database&limit=25"
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure or invalid query syntax

---

### 22. list_autonomous_databases

**Description:**
Lists Autonomous Database instances in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.autonomousdatabase.oc1...",
    "name": "atp-prod",
    "db_name": "PRODDB",
    "state": "AVAILABLE"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_autonomous_databases
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 23. list_db_systems

**Description:**
Lists Database Systems (traditional DBaaS) in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.dbsystem.oc1...",
    "name": "prod-db-system"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_db_systems
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 24. get_usage_summary

**Description:**
Retrieves usage and cost summary data for a time range. Dates must be in ISO 8601 format.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `time_start` | string | Required | Start time (ISO 8601 format, e.g., `2024-01-01T00:00:00Z`) |
| `time_end` | string | Required | End time (ISO 8601 format, e.g., `2024-01-31T23:59:59Z`) |

**Response Format:**
```json
[
  {
    "service": "Compute",
    "compartment_name": "development",
    "compartment_id": "ocid1.compartment.oc1...",
    "computed_amount": "1234.56",
    "computed_quantity": "730",
    "unit": "OCPU-hour",
    "currency": "USD",
    "time_usage_started": "2024-01-01 00:00:00",
    "time_usage_ended": "2024-01-31 23:59:59"
  }
]
```

**Example Usage:**
```bash
curl "http://localhost:8000/mcp/tools/get_usage_summary?time_start=2024-01-01T00:00:00Z&time_end=2024-01-31T23:59:59Z"
```

**Error Handling:**
- `500 Internal Server Error`: Invalid date format or OCI API failure

---

### 25. list_network_security_groups

**Description:**
Lists Network Security Groups (NSGs) in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.networksecuritygroup.oc1...",
    "name": "app-nsg",
    "state": "AVAILABLE"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_network_security_groups
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 26. list_load_balancers

**Description:**
Lists Load Balancers in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.loadbalancer.oc1...",
    "name": "my-lb",
    "shape": "100Mbps",
    "state": "ACTIVE"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_load_balancers
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 27. list_vaults

**Description:**
Lists Vault instances in the configured compartment.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.vault.oc1...",
    "name": "prod-vault"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_vaults
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

### 28. list_secrets

**Description:**
Lists secrets managed by the Vault service.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| (none) | - | - | No parameters |

**Response Format:**
```json
[
  {
    "id": "ocid1.secret.oc1...",
    "name": "db-password"
  }
]
```

**Example Usage:**
```bash
curl http://localhost:8000/mcp/tools/list_secrets
```

**Error Handling:**
- `500 Internal Server Error`: OCI API failure

---

## Prompt: oracle_context_prompt

**Name:** `oracle_context_prompt`

**Description:**
A system prompt that establishes the server's role and capabilities for AI agents.

**Prompt Content:**
```
You are an expert Oracle Cloud Infrastructure assistant powered by the Oracle Context MCP Server.
You have read access to Compute, Storage, Networking, Database, Identity, Vault, and more.
Always respect compartments and security.
Use OCIDs in answers.
When unsure, start with search_resources or server_health.
```

**Usage:**
This prompt is automatically provided to MCP clients that request it. AI agents can use this to understand the server's capabilities and apply proper context handling for OCI-specific queries.

**When to Use:**
- Multi-turn conversations requiring OCI context
- Agent framework initialization
- System role configuration in chat applications

---

## Error Handling

### HTTP Status Codes

The server returns standard HTTP status codes with detailed error information:

| Code | Meaning | When | Recovery |
|------|---------|------|----------|
| `200 OK` | Success | All successful tool calls | N/A |
| `400 Bad Request` | ServiceError from OCI | Invalid compartment, insufficient IAM permissions, bad parameters | Check IAM policy, verify compartment ID, re-validate parameters |
| `500 Internal Server Error` | Generic Exception | OCI SDK errors, network issues, invalid resource IDs | Check server logs (`oci_mcp_server.log`), verify OCI credentials, retry with exponential backoff |

### Error Response Format

All errors return structured JSON:

```json
{
  "detail": "Error message describing the issue"
}
```

**Example ServiceError (400):**
```json
{
  "detail": "User: <user_ocid> not authorized to perform: compute:ListInstances in compartment: <compartment_ocid>"
}
```

**Example Generic Exception (500):**
```json
{
  "detail": "Connection timeout while querying OCI API"
}
```

### Exception Types

1. **ServiceError** (from `oci.exceptions.ServiceError`)
   - Raised by OCI SDK when API returns error (4xx, 5xx HTTP)
   - Includes OCI error code and message
   - Common causes: IAM denials, invalid resource IDs, quota exceeded
   - Server converts to HTTP 400

2. **HTTPException** (from FastAPI)
   - Raised explicitly for validation errors
   - Custom status codes (400, 500)
   - Includes detail message

3. **Generic Exception**
   - Unhandled Python exceptions
   - Converted to HTTP 500 with stringified message
   - Server logs full traceback with loguru

### Logging

All exceptions are logged to `/home/runner/oci_mcp_server.log` (or `oci_mcp_server.log` in working directory):

```python
logger.add("oci_mcp_server.log", rotation="10 MB", level=os.getenv("LOG_LEVEL", "INFO"))
```

**Log Levels:**
- `DEBUG`: Detailed request/response traces
- `INFO`: Tool calls, successes (default)
- `WARNING`: Auth fallback, missing env vars
- `ERROR`: ServiceErrors, unhandled exceptions

**Set via environment:**
```bash
export LOG_LEVEL=DEBUG
```

**Security Note:** Logs never include credentials, API keys, or full request bodies. Set `LOG_LEVEL=ERROR` in production to reduce log verbosity.

---

## Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OCI_COMPARTMENT_ID` | string | None | Compartment OCID for resource queries. If not set, falls back to tenancy root compartment. |
| `OCI_REGION` | string | `us-phoenix-1` | OCI region code (e.g., `us-ashburn-1`, `eu-amsterdam-1`) |
| `LOG_LEVEL` | string | `INFO` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `OCI_CONFIG_FILE` | string | `~/.oci/config` | Path to OCI config file (only used if Instance Principal unavailable) |

### Example .env File

```env
OCI_COMPARTMENT_ID=ocid1.compartment.oc1.phx.aaaaaaaxxxxxxxxx
OCI_REGION=us-phoenix-1
LOG_LEVEL=INFO
```

### Environment Loading

The server uses `python-dotenv` to load `.env` file on startup:

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## Quick Start - 3 Examples

### Example 1: Check Server Health

```python
import httpx

client = httpx.Client()
response = client.get("http://localhost:8000/mcp/tools/server_health")
print(response.json())
# Output:
# {
#   "status": "healthy",
#   "server": "Oracle Context MCP Server v2.0",
#   "iam_mode": "InstancePrincipal",
#   "region": "us-phoenix-1",
#   "compartment_id": "ocid1.compartment.oc1..."
# }
```

### Example 2: List Compute Instances

```python
import httpx

client = httpx.Client()
response = client.get(
    "http://localhost:8000/mcp/tools/list_compute_instances",
    params={"limit": 10}
)
instances = response.json()
for instance in instances:
    print(f"{instance['name']}: {instance['state']}")
```

### Example 3: Search Resources and Get Usage

```python
import httpx
from datetime import datetime, timedelta

client = httpx.Client()

# Search for databases
search_response = client.get(
    "http://localhost:8000/mcp/tools/search_resources",
    params={"query": "database", "limit": 5}
)
databases = search_response.json()
print(f"Found {len(databases)} databases")

# Get last 30 days of usage
now = datetime.utcnow()
thirty_days_ago = now - timedelta(days=30)

usage_response = client.get(
    "http://localhost:8000/mcp/tools/get_usage_summary",
    params={
        "time_start": thirty_days_ago.isoformat() + "Z",
        "time_end": now.isoformat() + "Z"
    }
)
usage = usage_response.json()
total_cost = sum(float(item.get("computed_amount", 0)) for item in usage)
print(f"Total cost (30 days): ${total_cost:.2f}")
```

---

## Integration with MCP Clients

The server implements the MCP protocol over HTTP. To integrate with an MCP client:

### Claude Desktop / Claude Code

```json
{
  "oci": {
    "command": "python",
    "args": ["/path/to/mcp_server.py"],
    "env": {
      "OCI_COMPARTMENT_ID": "ocid1.compartment.oc1...",
      "OCI_REGION": "us-phoenix-1"
    }
  }
}
```

### Langchain Integration

```python
from langchain.chat_models import ChatOpenAI
from langchain.agents import load_tools, initialize_agent

# Load MCP tools
tools = load_tools(["mcp://localhost:8000/mcp"])

# Initialize agent
llm = ChatOpenAI(model="gpt-4")
agent = initialize_agent(tools, llm, agent="zero-shot-react-description")

# Query
result = agent.run("List all running compute instances in my tenancy")
```

### OCI Generative AI Agent

1. Deploy server via Terraform (see README.md)
2. Create OCI GenAI Agent in console
3. Add tool: **Custom > Model Context Protocol**
4. Configure:
   - **Remote MCP Server**: `http://<container-instance-ip>:8000/mcp`
   - **Authentication**: Instance Principal
5. Test via chat

---

## Limitations & Future Work

### Current Limitations

- **Read-Only**: All 28 tools are read-only (no create/update/delete operations)
- **Async**: All tools are async but block on OCI SDK calls (not fully non-blocking)
- **Single Compartment**: Hardcoded to one compartment per server instance
- **No Pagination**: Limit parameter controls result count but doesn't support cursor pagination

### Planned Enhancements

- Write operations (create instances, upload objects, provision databases)
- Kubernetes (OKE) support and deployment templates
- Custom tool builder for user-defined tools
- Cost optimization analyzer tool
- GitHub Actions integration for CI/CD automation
- Support for multi-compartment queries
- GraphQL API alongside REST

---

## Support & Troubleshooting

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
python mcp_server.py
```

### Test Authentication

```bash
curl http://localhost:8000/mcp/tools/get_tenancy_info
# Should return tenancy_id and auth_mode
```

### Verify Compartment Access

```bash
curl http://localhost:8000/mcp/tools/list_compartments
# If empty or error, check OCI_COMPARTMENT_ID and IAM policy
```

### Check Container Instance Health (if deployed)

```bash
# Get Container Instance IP
terraform output mcp_server_url

# Test
curl http://<ip>:8000/health
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `Instance Principal unavailable` | Running outside OCI or no Dynamic Group | Create Dynamic Group and IAM policy, or use `~/.oci/config` |
| `ServiceError: not authorized` | Insufficient IAM permissions | Review compartment policy, add missing `manage` grants |
| `Compartment ID required` | `OCI_COMPARTMENT_ID` not set | Set env var or pass `parent_id` parameter |
| `Connection timeout` | Network/firewall issue | Check security lists, NSGs, allow egress to OCI API endpoints |

---

## References

- [OCI SDK for Python Docs](https://docs.oracle.com/en-us/iaas/tools/python/latest/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [OCI API Reference](https://docs.oracle.com/en-us/iaas/api/)
- [OCI IAM Policies](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/overview.htm)
- [OCI Generative AI Service](https://docs.oracle.com/en-us/iaas/Content/generative-ai/home.htm)
