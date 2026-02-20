# OCI Context MCP Server - Full Implementation Instructions

Create a complete, production-ready Oracle Context MCP Server that gives AI agents secure access to OCI resources via the Model Context Protocol. This is the OCI-native equivalent of Azure MCP Server.

## Project Structure

```
oci-context-mcp-server/
├── mcp_server.py          # Main server - 28 tools across 10 OCI services
├── requirements.txt       # Python dependencies
├── Dockerfile             # Production container with uvicorn
├── .env.example           # Environment config template
├── .gitignore             # Already exists
├── infra/
│   ├── main.tf            # Terraform orchestration
│   ├── variables.tf       # Input variables
│   ├── outputs.tf         # Deployment outputs
│   └── provider.tf        # OCI provider config
├── docs/
│   └── oci-mcp-deployment-guide.html  # Full HTML deployment guide
├── tests/
│   └── test_health.py     # Basic health check test
└── README.md              # Project documentation
```

## File 1: requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
mcp[server]>=1.0.0
oci>=2.167.2
pydantic>=2.10.3
python-dotenv>=1.0.1
loguru>=0.7.3
httpx>=0.28.1
```

## File 2: .env.example

```
OCI_COMPARTMENT_ID=ocid1.compartment.oc1..xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OCI_REGION=us-phoenix-1
LOG_LEVEL=INFO
```

## File 3: Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .

EXPOSE 8000

CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## File 4: mcp_server.py (CORE - 28 tools)

This is the main server file. It must include:

### OCIAuthManager class:
- Instance Principal (keyless) as primary auth for OCI hosting
- Fallback to ~/.oci/config for local development
- Initialize ALL major OCI SDK clients:
  - identity_client (oci.identity.IdentityClient)
  - compute_client (oci.core.ComputeClient)
  - os_client (oci.object_storage.ObjectStorageClient)
  - network_client (oci.core.VirtualNetworkClient)
  - blockstorage_client (oci.core.BlockstorageClient)
  - filestorage_client (oci.file_storage.FileStorageClient)
  - search_client (oci.resource_search.ResourceSearchClient)
  - database_client (oci.database.DatabaseClient)
  - usage_client (oci.usage_api.UsageapiClient)
  - vault_client (oci.vault.VaultsClient)
- validate_iam_access() method hook for future policy simulation

### FastAPI + MCP setup:
- FastAPI app with CORS middleware
- OAuth2 bearer token verification (Instance Principal bypasses, external clients need token)
- FastMCP("oci-context-server") mounted at /mcp

### All 28 MCP Tools organized by service:

**Health & Tenancy (3):**
1. server_health - Health check + IAM status
2. get_tenancy_info - Tenancy, user, auth context
3. list_regions - All OCI regions available

**Compute (3):**
4. list_compute_instances - List instances with id, name, shape, state
5. list_compute_shapes - Available shapes with ocpus, memory
6. get_compute_instance - Get specific instance by OCID

**Object Storage (4):**
7. get_object_storage_namespace - Get tenancy namespace
8. list_buckets - All buckets with name, created date
9. list_objects - Objects in a bucket (takes bucket_name, limit)
10. get_bucket_details - Bucket details including public access type

**Identity & IAM (4):**
11. list_compartments - List compartments (optional parent_id)
12. list_users - IAM users
13. list_groups - IAM groups
14. list_policies - IAM policies with statements

**Networking (4):**
15. list_vcns - VCNs with id, name, cidr
16. list_subnets - Subnets (optional vcn_id filter)
17. list_security_lists - Security lists
18. list_route_tables - Route tables

**Block & File Storage (2):**
19. list_block_volumes - Block volumes with size
20. list_file_systems - File storage systems

**Resource Search (1):**
21. search_resources - Free-text search across all OCI resources (uses FreeTextSearchDetails)

**Database (2):**
22. list_autonomous_databases - ADB with db_name, state
23. list_db_systems - DB Systems (Exadata etc)

**Monitoring (1):**
24. get_usage_summary - Cost/usage summary (takes time_start, time_end in ISO format)

**Vault & Secrets (2):**
25. list_vaults - Vaults
26. list_secrets - Secrets in vault

**Prompt (1):**
27. oracle_context_prompt - System prompt for AI agents

**Routes:**
28. Root "/" returns server info, "/health" returns status with tool count

### Each tool must:
- Use async def
- Accept Context as first param
- Return json.dumps() with default=str
- Handle ServiceError with HTTPException
- Call auth_manager.validate_iam_access() where appropriate
- Have clear docstrings for LLM agent usage

## File 5: infra/provider.tf

```hcl
terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0.0"
    }
  }
}

provider "oci" {
  region           = var.region
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
}
```

## File 6: infra/variables.tf

```hcl
variable "tenancy_ocid" { description = "OCI Tenancy OCID" }
variable "user_ocid" { description = "OCI User OCID" }
variable "fingerprint" { description = "API Key Fingerprint" }
variable "private_key_path" { description = "Path to private key" }
variable "region" { default = "us-phoenix-1" }
variable "compartment_id" { description = "Compartment OCID for resources" }
variable "vcn_id" { description = "VCN OCID" }
variable "subnet_id" { description = "Subnet OCID" }
variable "ocir_image_url" { description = "OCIR image URL for MCP server" }
```

## File 7: infra/main.tf

Must include:
- Dynamic group for Instance Principal (oci_identity_dynamic_group)
- IAM policies for compute, object-storage, identity read access (oci_identity_policy)
- Container Instance resource (oci_container_instances_container_instance) with:
  - CI.Standard.E4.Flex shape (1 OCPU, 4GB)
  - Environment variables for OCI_COMPARTMENT_ID and OCI_REGION
  - Port 8000 exposed
  - Image from OCIR variable

## File 8: infra/outputs.tf

```hcl
output "mcp_server_url" {
  description = "MCP Server endpoint URL"
  value       = "http://${oci_container_instances_container_instance.mcp_instance.vnics[0].private_ip}:8000/mcp"
}

output "container_instance_id" {
  value = oci_container_instances_container_instance.mcp_instance.id
}
```

## File 9: docs/oci-mcp-deployment-guide.html

Full HTML deployment guide styled with Oracle blue (#0073bb). Include:
- Prerequisites section
- Step-by-step deployment (Docker build, OCIR push, Terraform apply)
- OCI Generative AI Agent integration steps
- API Gateway fronting for public access
- Cleanup instructions (terraform destroy)
- Terraform module descriptions
- API references with OCI doc links
- Troubleshooting section

## File 10: tests/test_health.py

Simple pytest test that:
- Tests the /health endpoint returns 200
- Tests the / root endpoint returns server info
- Uses httpx AsyncClient with FastAPI TestClient pattern

## File 11: README.md

Include:
- Project title and description
- Features list (28 tools, 10 services, IAM integration)
- Quick start (local dev with python mcp_server.py)
- Docker deployment steps
- Terraform deployment steps
- Tool inventory table
- OCI Generative AI Agent connection instructions
- API references
- License (MIT)
