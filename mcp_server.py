import os
import json
import asyncio
from typing import Optional
from dotenv import load_dotenv
from loguru import logger
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from mcp.server.fastmcp import FastMCP, Context
import oci
from oci.exceptions import ServiceError

load_dotenv()

# ====================== LOGGING ======================
logger.add("oci_mcp_server.log", rotation="10 MB", level=os.getenv("LOG_LEVEL", "INFO"))

# ====================== OCI AUTH MANAGER ======================
class OCIAuthManager:
    def __init__(self):
        self.compartment_id = os.getenv("OCI_COMPARTMENT_ID")
        self.region = os.getenv("OCI_REGION", "us-phoenix-1")

        try:
            self.signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            logger.success("✅ Instance Principal IAM enabled")
            self.using_instance_principal = True
        except Exception as e:
            logger.warning(f"Instance Principal unavailable: {e}. Using config file.")
            config = oci.config.from_file()
            self.signer = oci.signer.Signer.from_config(config)
            self.using_instance_principal = False

        # Initialize ALL clients
        self.identity_client = oci.identity.IdentityClient({}, signer=self.signer)
        self.compute_client = oci.core.ComputeClient({}, signer=self.signer)
        self.os_client = oci.object_storage.ObjectStorageClient({}, signer=self.signer)
        self.network_client = oci.core.VirtualNetworkClient({}, signer=self.signer)
        self.blockstorage_client = oci.core.BlockstorageClient({}, signer=self.signer)
        self.filestorage_client = oci.file_storage.FileStorageClient({}, signer=self.signer)
        self.search_client = oci.resource_search.ResourceSearchClient({}, signer=self.signer)
        self.database_client = oci.database.DatabaseClient({}, signer=self.signer)
        self.usage_client = oci.usage_api.UsageapiClient({}, signer=self.signer)
        self.vault_client = oci.vault.VaultsClient({}, signer=self.signer)
        self.lb_client = oci.load_balancer.LoadBalancerClient({}, signer=self.signer)

    def validate_iam_access(self, action: str = "read"):
        """IAM validation hook"""
        logger.info(f"IAM validation for action: {action}")
        return True

auth_manager = OCIAuthManager()

# ====================== FASTAPI + MCP ======================
app = FastAPI(title="Oracle Context MCP Server", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def verify_iam_token(authorization: Optional[str] = Header(None)):
    return {"auth_mode": "InstancePrincipal" if auth_manager.using_instance_principal else "OAuth2"}

mcp = FastMCP("oci-context-server", dependencies=[Depends(verify_iam_token)])

# ====================== HELPER: Get root compartment ======================
async def get_root_compartment() -> str:
    if auth_manager.compartment_id:
        return auth_manager.compartment_id
    try:
        tenancy = auth_manager.identity_client.get_tenancy(auth_manager.signer.tenancy_id if hasattr(auth_manager.signer, 'tenancy_id') else "ocid1.tenancy.oc1..example").data
        return tenancy.id
    except Exception:
        raise HTTPException(status_code=400, detail="Compartment ID required")

# ====================== TOOL 1-3: HEALTH & TENANCY ======================
@mcp.tool()
async def server_health(ctx: Context) -> str:
    """Health check and IAM status."""
    return json.dumps({
        "status": "healthy",
        "server": "Oracle Context MCP Server v2.0",
        "iam_mode": "InstancePrincipal" if auth_manager.using_instance_principal else "Config",
        "region": auth_manager.region,
        "compartment_id": auth_manager.compartment_id
    })

@mcp.tool()
async def get_tenancy_info(ctx: Context) -> str:
    """Get tenancy, user, and authentication context."""
    try:
        return json.dumps({
            "tenancy_id": getattr(auth_manager.signer, 'tenancy_id', 'N/A'),
            "region": auth_manager.region,
            "compartment_id": auth_manager.compartment_id,
            "auth_mode": "Instance Principal" if auth_manager.using_instance_principal else "API Key"
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
async def list_regions(ctx: Context) -> str:
    """List all OCI regions."""
    try:
        regions = auth_manager.identity_client.list_regions().data
        return json.dumps([{"name": r.name, "key": r.key} for r in regions], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 4-6: COMPUTE ======================
@mcp.tool()
async def list_compute_instances(ctx: Context, limit: int = 100) -> str:
    """List Compute Instances."""
    auth_manager.validate_iam_access("read")
    try:
        resp = auth_manager.compute_client.list_instances(compartment_id=auth_manager.compartment_id, limit=limit).data
        return json.dumps([{"id": i.id, "name": i.display_name, "shape": i.shape, "state": i.lifecycle_state} for i in resp], default=str)
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

@mcp.tool()
async def list_compute_shapes(ctx: Context) -> str:
    """List available Compute shapes."""
    try:
        shapes = auth_manager.compute_client.list_shapes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"shape": s.shape, "ocpus": s.ocpus, "memory_gb": s.memory_in_gbs} for s in shapes], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def get_compute_instance(ctx: Context, instance_id: str) -> str:
    """Get details of a specific Compute instance."""
    try:
        inst = auth_manager.compute_client.get_instance(instance_id=instance_id).data
        return json.dumps({"id": inst.id, "name": inst.display_name, "shape": inst.shape, "state": inst.lifecycle_state}, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 7-10: OBJECT STORAGE ======================
@mcp.tool()
async def get_object_storage_namespace(ctx: Context) -> str:
    """Get tenancy Object Storage namespace."""
    try:
        ns = auth_manager.os_client.get_namespace().data
        return json.dumps({"namespace": ns})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_buckets(ctx: Context) -> str:
    """List all Object Storage buckets."""
    auth_manager.validate_iam_access("read")
    try:
        ns = auth_manager.os_client.get_namespace().data
        buckets = auth_manager.os_client.list_buckets(namespace_name=ns, compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"name": b.name, "created": str(b.time_created)} for b in buckets], default=str)
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

@mcp.tool()
async def list_objects(ctx: Context, bucket_name: str, limit: int = 100) -> str:
    """List objects in a bucket."""
    auth_manager.validate_iam_access("read")
    try:
        ns = auth_manager.os_client.get_namespace().data
        objs = auth_manager.os_client.list_objects(namespace_name=ns, bucket_name=bucket_name, limit=limit).data.objects
        return json.dumps([{"name": o.name, "size_bytes": o.size} for o in objs], default=str)
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

@mcp.tool()
async def get_bucket_details(ctx: Context, bucket_name: str) -> str:
    """Get details of a specific bucket."""
    try:
        ns = auth_manager.os_client.get_namespace().data
        bucket = auth_manager.os_client.get_bucket(namespace_name=ns, bucket_name=bucket_name).data
        return json.dumps({"name": bucket.name, "created": str(bucket.time_created), "public": bucket.public_access_type}, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 11-14: IDENTITY ======================
@mcp.tool()
async def list_compartments(ctx: Context, parent_id: Optional[str] = None) -> str:
    """List compartments."""
    try:
        cid = parent_id or auth_manager.compartment_id
        comps = auth_manager.identity_client.list_compartments(compartment_id=cid).data
        return json.dumps([{"id": c.id, "name": c.name} for c in comps], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_users(ctx: Context) -> str:
    """List IAM users."""
    try:
        users = auth_manager.identity_client.list_users(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": u.id, "name": u.name} for u in users], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_groups(ctx: Context) -> str:
    """List IAM groups."""
    try:
        groups = auth_manager.identity_client.list_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": g.id, "name": g.name} for g in groups], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_policies(ctx: Context) -> str:
    """List IAM policies."""
    try:
        policies = auth_manager.identity_client.list_policies(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": p.id, "name": p.name, "statements": p.statements} for p in policies], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 15-18: NETWORKING ======================
@mcp.tool()
async def list_vcns(ctx: Context) -> str:
    """List Virtual Cloud Networks."""
    try:
        vcns = auth_manager.network_client.list_vcns(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name, "cidr": v.cidr_block} for v in vcns], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_subnets(ctx: Context, vcn_id: Optional[str] = None) -> str:
    """List subnets (optionally filter by VCN)."""
    try:
        subnets = auth_manager.network_client.list_subnets(compartment_id=auth_manager.compartment_id, vcn_id=vcn_id).data
        return json.dumps([{"id": s.id, "name": s.display_name, "cidr": s.cidr_block} for s in subnets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_security_lists(ctx: Context) -> str:
    """List Security Lists."""
    try:
        sls = auth_manager.network_client.list_security_lists(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": sl.id, "name": sl.display_name} for sl in sls], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_route_tables(ctx: Context) -> str:
    """List Route Tables."""
    try:
        rts = auth_manager.network_client.list_route_tables(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": rt.id, "name": rt.display_name} for rt in rts], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 19-20: BLOCK & FILE STORAGE ======================
@mcp.tool()
async def list_block_volumes(ctx: Context) -> str:
    """List Block Volumes."""
    try:
        vols = auth_manager.blockstorage_client.list_volumes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name, "size_gb": v.size_in_gbs} for v in vols], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_file_systems(ctx: Context, availability_domain: Optional[str] = None) -> str:
    """List File Storage systems. availability_domain is required by OCI API (e.g. 'AD-1')."""
    try:
        if not availability_domain:
            # Attempt to resolve first AD in the region
            ads = auth_manager.identity_client.list_availability_domains(compartment_id=auth_manager.compartment_id).data
            if not ads:
                raise HTTPException(status_code=400, detail="availability_domain required and could not be auto-resolved")
            availability_domain = ads[0].name
        fs = auth_manager.filestorage_client.list_file_systems(
            compartment_id=auth_manager.compartment_id,
            availability_domain=availability_domain
        ).data
        return json.dumps([{"id": f.id, "name": f.display_name} for f in fs], default=str)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 21: SEARCH ======================
@mcp.tool()
async def search_resources(ctx: Context, query: str, limit: int = 50) -> str:
    """Free-text search across all OCI resources."""
    try:
        details = oci.resource_search.models.FreeFormSearchDetails(text=query)
        resp = auth_manager.search_client.search_resources(search_details=details, limit=limit).data
        resources = [{"type": r.resource_type, "id": r.identifier, "name": getattr(r, "display_name", None)} for r in resp.resources]
        return json.dumps(resources, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 22-23: DATABASE ======================
@mcp.tool()
async def list_autonomous_databases(ctx: Context) -> str:
    """List Autonomous Databases."""
    try:
        dbs = auth_manager.database_client.list_autonomous_databases(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": db.id, "name": db.display_name, "db_name": db.db_name, "state": db.lifecycle_state} for db in dbs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_db_systems(ctx: Context) -> str:
    """List DB Systems."""
    try:
        dbs = auth_manager.database_client.list_db_systems(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": db.id, "name": db.display_name} for db in dbs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 24: USAGE ======================
@mcp.tool()
async def get_usage_summary(ctx: Context, time_start: str, time_end: str) -> str:
    """Get usage/cost summary (dates in ISO format)."""
    try:
        details = oci.usage_api.models.SummarizeUsageDetails(time_usage_started=time_start, time_usage_ended=time_end)
        usage = auth_manager.usage_client.summarize_usage(compartment_id=auth_manager.compartment_id, summarize_usage_details=details).data
        return json.dumps([{
            "service": getattr(u, "service", None),
            "compartment_name": getattr(u, "compartment_name", None),
            "compartment_id": getattr(u, "compartment_id", None),
            "computed_amount": getattr(u, "computed_amount", None),
            "computed_quantity": getattr(u, "computed_quantity", None),
            "unit": getattr(u, "unit", None),
            "currency": getattr(u, "currency", None),
            "time_usage_started": str(getattr(u, "time_usage_started", None)),
            "time_usage_ended": str(getattr(u, "time_usage_ended", None)),
        } for u in usage.items], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 25-26: NETWORK SECURITY GROUPS & LOAD BALANCERS ======================
@mcp.tool()
async def list_network_security_groups(ctx: Context) -> str:
    """List Network Security Groups (NSGs)."""
    try:
        nsgs = auth_manager.network_client.list_network_security_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": n.id, "name": n.display_name, "state": n.lifecycle_state} for n in nsgs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_load_balancers(ctx: Context) -> str:
    """List Load Balancers."""
    try:
        lbs = auth_manager.lb_client.list_load_balancers(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": lb.id, "name": lb.display_name, "shape": lb.shape_name, "state": lb.lifecycle_state} for lb in lbs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 27-28: VAULT ======================
@mcp.tool()
async def list_vaults(ctx: Context) -> str:
    """List Vaults."""
    try:
        vaults = auth_manager.vault_client.list_vaults(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name} for v in vaults], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
async def list_secrets(ctx: Context) -> str:
    """List Secrets (Vault)."""
    try:
        secrets = auth_manager.vault_client.list_secrets(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": s.id, "name": s.secret_name} for s in secrets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== PROMPT ======================
@mcp.prompt()
def oracle_context_prompt(ctx: Context) -> str:
    """Oracle Context system prompt for AI agents."""
    return """You are an expert Oracle Cloud Infrastructure assistant powered by the Oracle Context MCP Server.
You have read access to Compute, Storage, Networking, Database, Identity, Vault, and more.
Always respect compartments and security.
Use OCIDs in answers.
When unsure, start with search_resources or server_health."""

# ====================== ROUTES & MOUNT ======================
mcp.mount(app, "/mcp")

@app.get("/")
async def root():
    return {"message": "Oracle Context MCP Server v2.0", "tools": 28, "endpoint": "/mcp"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools_count": 28, "iam": "InstancePrincipal" if auth_manager.using_instance_principal else "Config"}


# ====================== CLI ENTRY POINT ======================
import click
import sys


def _print_claude_config():
    config = {
        "mcpServers": {
            "oci": {
                "command": "uvx",
                "args": ["oci-context-mcp", "--transport", "stdio"],
                "env": {
                    "OCI_COMPARTMENT_ID": "<your-compartment-ocid>",
                    "OCI_REGION": "us-phoenix-1"
                }
            }
        }
    }
    print(json.dumps(config, indent=2))


@click.command()
@click.option("--transport", default="http",
              type=click.Choice(["http", "stdio"]),
              help="Transport: 'http' for remote/Docker, 'stdio' for Claude Desktop/Cursor/VS Code")
@click.option("--port", default=8000, show_default=True, help="HTTP port (http mode only)")
@click.option("--host", default="0.0.0.0", show_default=True, help="HTTP host (http mode only)")
@click.option("--print-config", is_flag=True,
              help="Print ready-to-paste Claude Desktop mcpServers config and exit")
def cli(transport, port, host, print_config):
    """Oracle Context MCP Server — 28 OCI read tools for AI agents.

    \b
    Quick start (Claude Desktop / Cursor):
      uvx oci-context-mcp --print-config   # prints your claude_desktop_config.json snippet
      uvx oci-context-mcp --transport stdio

    \b
    Remote / Docker:
      python mcp_server.py --transport http --port 8000
    """
    if print_config:
        _print_claude_config()
        return

    if transport == "stdio":
        # STDIO mode: stdout is the MCP wire protocol — never write to stdout
        logger.remove()
        logger.add(sys.stderr, level="WARNING", colorize=False)
        logger.add("oci_mcp_server.log", rotation="10 MB",
                   level=os.getenv("LOG_LEVEL", "INFO"))
        mcp.run()  # FastMCP built-in STDIO transport
    else:
        import uvicorn
        logger.info("Starting Oracle Context MCP Server (HTTP) with 28 tools on {}:{}", host, port)
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
