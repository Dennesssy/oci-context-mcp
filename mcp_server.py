import os
import json
import asyncio
import functools
import hashlib
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List
from dotenv import load_dotenv
from loguru import logger
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from mcp.server.fastmcp import FastMCP, Context
import oci
from oci.exceptions import ServiceError

load_dotenv()

# ====================== TELEMETRY ======================
# Set OCI_MCP_TELEMETRY=off to disable. No data leaves the host.
# Events are written to OCI_MCP_METRICS_FILE (default: oci_mcp_metrics.jsonl).
_TELEMETRY_MODE = os.getenv("OCI_MCP_TELEMETRY", "local")   # local | off
_METRICS_FILE   = os.getenv("OCI_MCP_METRICS_FILE", "oci_mcp_metrics.jsonl")
_SERVER_VERSION = "2.5.0"


def _record(tool_name: str, ok: bool, duration_ms: float, error: str = None) -> None:
    """Append one JSON event to the local metrics file. Never raises."""
    if _TELEMETRY_MODE == "off":
        return
    try:
        event = {
            "ts":           _time.time(),
            "tool":         tool_name,
            "ok":           ok,
            "ms":           round(duration_ms),
            "version":      _SERVER_VERSION,
            # hashed region — identifies unique deploys without exposing tenant details
            "region_hash":  hashlib.sha256(
                                os.getenv("OCI_REGION", "unknown").encode()
                            ).hexdigest()[:8],
        }
        if error:
            event["error"] = error[:120]
        with open(_METRICS_FILE, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass  # telemetry must never crash the server


def tracked(fn):
    """Decorator: record tool name, success/failure, and latency to metrics file."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        t0 = _time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            _record(fn.__name__, ok=True,
                    duration_ms=(_time.monotonic() - t0) * 1000)
            return result
        except Exception as exc:
            _record(fn.__name__, ok=False,
                    duration_ms=(_time.monotonic() - t0) * 1000,
                    error=str(exc))
            raise
    return wrapper


# ====================== COMPARTMENT TREE ======================
@dataclass
class CompartmentNode:
    id: str
    name: str
    lifecycle_state: str
    children: List["CompartmentNode"] = field(default_factory=list)
    depth: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "lifecycle_state": self.lifecycle_state,
            "depth": self.depth,
            "children": [c.to_dict() for c in self.children],
        }


_tree_cache: dict = {}  # {root_id: (CompartmentNode, expires_at)}


async def build_compartment_tree(root_id: str, max_depth: int = 5) -> CompartmentNode:
    """Recursively build compartment hierarchy with a 5-minute in-memory cache."""
    cached = _tree_cache.get(root_id)
    if cached and cached[1] > datetime.utcnow():
        return cached[0]

    async def _fetch(parent_id: str, depth: int) -> List[CompartmentNode]:
        if depth >= max_depth:
            return []
        try:
            resp = auth_manager.identity_client.list_compartments(
                compartment_id=parent_id,
                lifecycle_state="ACTIVE",
            ).data
        except Exception:
            return []
        nodes: List[CompartmentNode] = []
        tasks = []
        for c in resp:
            node = CompartmentNode(
                id=c.id, name=c.name,
                lifecycle_state=c.lifecycle_state, depth=depth
            )
            nodes.append(node)
            tasks.append(_fetch(c.id, depth + 1))
        children_lists = await asyncio.gather(*tasks)
        for node, children in zip(nodes, children_lists):
            node.children = children
        return nodes

    root = CompartmentNode(id=root_id, name="root", lifecycle_state="ACTIVE")
    root.children = await _fetch(root_id, 0)
    _tree_cache[root_id] = (root, datetime.utcnow() + timedelta(minutes=5))
    return root


def flatten_compartment_ids(node: CompartmentNode) -> List[str]:
    ids = [node.id]
    for child in node.children:
        ids.extend(flatten_compartment_ids(child))
    return ids


async def resolve_compartment_ids(scope: str) -> List[str]:
    """Resolve compartment_scope to a list of OCIDs.

    scope values:
      single   — current OCI_COMPARTMENT_ID only (default, fast)
      recursive — current compartment + all child compartments
      tenancy  — root tenancy + entire hierarchy
    """
    root = auth_manager.compartment_id
    if scope == "tenancy":
        root = getattr(auth_manager.signer, "tenancy_id", root)
    if scope == "single":
        return [root]
    tree = await build_compartment_tree(root)
    return flatten_compartment_ids(tree)


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
        self.monitoring_client = oci.monitoring.MonitoringClient({}, signer=self.signer)
        self.logging_client = oci.logging.LoggingManagementClient({}, signer=self.signer)
        self.log_search_client = oci.loggingsearch.LogSearchClient({}, signer=self.signer)
        # Extended clients (v2.5)
        self.compute_mgmt_client = oci.core.ComputeManagementClient({}, signer=self.signer)
        self.container_engine_client = oci.container_engine.ContainerEngineClient({}, signer=self.signer)
        self.container_instances_client = oci.container_instances.ContainerInstanceClient({}, signer=self.signer)
        self.artifacts_client = oci.artifacts.ArtifactsClient({}, signer=self.signer)
        self.functions_client = oci.functions.FunctionsManagementClient({}, signer=self.signer)
        self.events_client = oci.events.EventsClient({}, signer=self.signer)
        self.ons_cp_client = oci.ons.NotificationControlPlaneClient({}, signer=self.signer)
        self.ons_dp_client = oci.ons.NotificationDataPlaneClient({}, signer=self.signer)
        self.dns_client = oci.dns.DnsClient({}, signer=self.signer)
        self.budget_client = oci.budgets.BudgetClient({}, signer=self.signer)
        self.audit_client = oci.audit.AuditClient({}, signer=self.signer)
        self.api_gw_client = oci.apigateway.ApiGatewayClient({}, signer=self.signer)
        self.api_deploy_client = oci.apigateway.DeploymentClient({}, signer=self.signer)
        self.bastion_client = oci.bastion.BastionClient({}, signer=self.signer)
        self.mysql_client = oci.mysql.DbSystemClient({}, signer=self.signer)
        self.nosql_client = oci.nosql.NosqlClient({}, signer=self.signer)
        self.devops_client = oci.devops.DevopsClient({}, signer=self.signer)

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
@tracked
async def server_health(ctx: Context) -> str:
    """Health check and IAM status."""
    return json.dumps({
        "status": "healthy",
        "server": "Oracle Context MCP Server v2.5",
        "tools": 69,
        "iam_mode": "InstancePrincipal" if auth_manager.using_instance_principal else "Config",
        "region": auth_manager.region,
        "compartment_id": auth_manager.compartment_id
    })

@mcp.tool()
@tracked
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
@tracked
async def list_regions(ctx: Context) -> str:
    """List all OCI regions."""
    try:
        regions = auth_manager.identity_client.list_regions().data
        return json.dumps([{"name": r.name, "key": r.key} for r in regions], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 4-6: COMPUTE ======================
@mcp.tool()
@tracked
async def list_compute_instances(
    ctx: Context,
    limit: int = 100,
    compartment_scope: str = "single",
) -> str:
    """List Compute Instances.

    compartment_scope: 'single' (default) | 'recursive' (include sub-compartments) | 'tenancy'
    """
    auth_manager.validate_iam_access("read")
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                resp = auth_manager.compute_client.list_instances(
                    compartment_id=cid, limit=limit).data
                results.extend([
                    {"id": i.id, "name": i.display_name, "shape": i.shape,
                     "state": i.lifecycle_state, "compartment_id": cid}
                    for i in resp
                ])
            except ServiceError:
                pass
        return json.dumps(results, default=str)
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

@mcp.tool()
@tracked
async def list_compute_shapes(ctx: Context) -> str:
    """List available Compute shapes."""
    try:
        shapes = auth_manager.compute_client.list_shapes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"shape": s.shape, "ocpus": s.ocpus, "memory_gb": s.memory_in_gbs} for s in shapes], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def get_compute_instance(ctx: Context, instance_id: str) -> str:
    """Get details of a specific Compute instance."""
    try:
        inst = auth_manager.compute_client.get_instance(instance_id=instance_id).data
        return json.dumps({"id": inst.id, "name": inst.display_name, "shape": inst.shape, "state": inst.lifecycle_state}, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 7-10: OBJECT STORAGE ======================
@mcp.tool()
@tracked
async def get_object_storage_namespace(ctx: Context) -> str:
    """Get tenancy Object Storage namespace."""
    try:
        ns = auth_manager.os_client.get_namespace().data
        return json.dumps({"namespace": ns})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_buckets(ctx: Context, compartment_scope: str = "single") -> str:
    """List all Object Storage buckets.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    auth_manager.validate_iam_access("read")
    try:
        ns = auth_manager.os_client.get_namespace().data
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                buckets = auth_manager.os_client.list_buckets(
                    namespace_name=ns, compartment_id=cid).data
                results.extend([
                    {"name": b.name, "created": str(b.time_created), "compartment_id": cid}
                    for b in buckets
                ])
            except ServiceError:
                pass
        return json.dumps(results, default=str)
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=e.message)

@mcp.tool()
@tracked
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
@tracked
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
@tracked
async def list_compartments(ctx: Context, parent_id: Optional[str] = None) -> str:
    """List compartments."""
    try:
        cid = parent_id or auth_manager.compartment_id
        comps = auth_manager.identity_client.list_compartments(compartment_id=cid).data
        return json.dumps([{"id": c.id, "name": c.name} for c in comps], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_users(ctx: Context) -> str:
    """List IAM users."""
    try:
        users = auth_manager.identity_client.list_users(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": u.id, "name": u.name} for u in users], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_groups(ctx: Context) -> str:
    """List IAM groups."""
    try:
        groups = auth_manager.identity_client.list_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": g.id, "name": g.name} for g in groups], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_policies(ctx: Context) -> str:
    """List IAM policies."""
    try:
        policies = auth_manager.identity_client.list_policies(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": p.id, "name": p.name, "statements": p.statements} for p in policies], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 15-16: COMPARTMENT TREE (F2) ======================
@mcp.tool()
@tracked
async def get_compartment_tree(ctx: Context, max_depth: int = 5) -> str:
    """Return the full compartment hierarchy as a JSON tree (cached 5 min).
    Useful for discovering which compartments exist before scoping queries."""
    try:
        root_id = auth_manager.compartment_id
        tree = await build_compartment_tree(root_id, max_depth=max_depth)
        return json.dumps(tree.to_dict(), default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def resolve_compartment_by_name(ctx: Context, name: str) -> str:
    """Find a compartment OCID by display name (case-insensitive, searches full tree).
    Returns all matches if multiple compartments share the same name."""
    try:
        root_id = auth_manager.compartment_id
        tree = await build_compartment_tree(root_id)
        matches = []

        def _search(node: CompartmentNode):
            if node.name.lower() == name.lower():
                matches.append({"id": node.id, "name": node.name,
                                 "lifecycle_state": node.lifecycle_state,
                                 "depth": node.depth})
            for child in node.children:
                _search(child)

        _search(tree)
        return json.dumps({"query": name, "matches": matches, "count": len(matches)})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== TOOL 17-20: NETWORKING ======================
@mcp.tool()
@tracked
async def list_vcns(ctx: Context, compartment_scope: str = "single") -> str:
    """List Virtual Cloud Networks.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                vcns = auth_manager.network_client.list_vcns(compartment_id=cid).data
                results.extend([
                    {"id": v.id, "name": v.display_name, "cidr": v.cidr_block,
                     "compartment_id": cid}
                    for v in vcns
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_subnets(ctx: Context, vcn_id: Optional[str] = None) -> str:
    """List subnets (optionally filter by VCN)."""
    try:
        subnets = auth_manager.network_client.list_subnets(compartment_id=auth_manager.compartment_id, vcn_id=vcn_id).data
        return json.dumps([{"id": s.id, "name": s.display_name, "cidr": s.cidr_block} for s in subnets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_security_lists(ctx: Context) -> str:
    """List Security Lists."""
    try:
        sls = auth_manager.network_client.list_security_lists(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": sl.id, "name": sl.display_name} for sl in sls], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_route_tables(ctx: Context) -> str:
    """List Route Tables."""
    try:
        rts = auth_manager.network_client.list_route_tables(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": rt.id, "name": rt.display_name} for rt in rts], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 19-20: BLOCK & FILE STORAGE ======================
@mcp.tool()
@tracked
async def list_block_volumes(ctx: Context) -> str:
    """List Block Volumes."""
    try:
        vols = auth_manager.blockstorage_client.list_volumes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name, "size_gb": v.size_in_gbs} for v in vols], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
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
@tracked
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
@tracked
async def list_autonomous_databases(ctx: Context, compartment_scope: str = "single") -> str:
    """List Autonomous Databases.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                dbs = auth_manager.database_client.list_autonomous_databases(
                    compartment_id=cid).data
                results.extend([
                    {"id": db.id, "name": db.display_name, "db_name": db.db_name,
                     "state": db.lifecycle_state, "compartment_id": cid}
                    for db in dbs
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_db_systems(ctx: Context, compartment_scope: str = "single") -> str:
    """List DB Systems.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                dbs = auth_manager.database_client.list_db_systems(compartment_id=cid).data
                results.extend([
                    {"id": db.id, "name": db.display_name, "compartment_id": cid}
                    for db in dbs
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 24: USAGE ======================
@mcp.tool()
@tracked
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

# ====================== TOOL 25-31: MONITORING & LOGGING (F3) ======================
@mcp.tool()
@tracked
async def list_metric_namespaces(ctx: Context, compartment_scope: str = "single") -> str:
    """List available OCI Monitoring metric namespaces (e.g. oci_computeagent, oci_lbaas).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        namespaces: set = set()
        for cid in compartment_ids:
            try:
                details = oci.monitoring.models.ListMetricsDetails()
                resp = auth_manager.monitoring_client.list_metrics(
                    compartment_id=cid,
                    list_metrics_details=details,
                ).data
                for m in resp:
                    namespaces.add(m.namespace)
            except Exception:
                pass
        return json.dumps(sorted(namespaces))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def query_metrics(
    ctx: Context,
    namespace: str,
    metric_name: str,
    time_start: str,
    time_end: str,
    interval: str = "PT5M",
    statistic: str = "mean",
    resource_id: Optional[str] = None,
) -> str:
    """Query OCI Monitoring time-series metrics.

    namespace:   e.g. 'oci_computeagent'
    metric_name: e.g. 'CpuUtilization'
    time_start / time_end: ISO-8601 datetime strings
    interval:    ISO-8601 duration, default PT5M (5 minutes)
    statistic:   mean | max | min | sum | count
    resource_id: optional OCID to filter to a single resource
    """
    try:
        if resource_id:
            query = f'{metric_name}[{interval}]{{resourceId = "{resource_id}"}}.{statistic}()'
        else:
            query = f"{metric_name}[{interval}].{statistic}()"
        details = oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace=namespace,
            query=query,
            start_time=time_start,
            end_time=time_end,
        )
        resp = auth_manager.monitoring_client.summarize_metrics_data(
            compartment_id=auth_manager.compartment_id,
            summarize_metrics_data_details=details,
        ).data
        return json.dumps([
            {
                "name": m.name,
                "namespace": m.namespace,
                "resource_group": getattr(m, "resource_group", None),
                "datapoints": [
                    {"timestamp": str(d.timestamp), "value": d.value}
                    for d in m.aggregated_datapoints
                ],
            }
            for m in resp
        ], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_alarms(
    ctx: Context,
    lifecycle_state: Optional[str] = None,
    compartment_scope: str = "single",
) -> str:
    """List OCI Monitoring alarm definitions.

    lifecycle_state: ACTIVE | INACTIVE (omit for all)
    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                kwargs = {"compartment_id": cid}
                if lifecycle_state:
                    kwargs["lifecycle_state"] = lifecycle_state
                alarms = auth_manager.monitoring_client.list_alarms(**kwargs).data
                results.extend([
                    {
                        "id": a.id,
                        "name": a.display_name,
                        "namespace": a.metric_compartment_id_in_subtree,
                        "query": a.query,
                        "severity": a.severity,
                        "is_enabled": a.is_enabled,
                        "lifecycle_state": a.lifecycle_state,
                        "compartment_id": cid,
                    }
                    for a in alarms
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def get_alarm_status(ctx: Context, compartment_scope: str = "single") -> str:
    """Get current firing status for all alarms (OK / FIRING / SUSPENDED).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                statuses = auth_manager.monitoring_client.list_alarms_status(
                    compartment_id=cid).data
                results.extend([
                    {
                        "id": s.id,
                        "name": s.display_name,
                        "status": s.status,
                        "severity": s.severity,
                        "compartment_id": cid,
                    }
                    for s in statuses
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_alarm_history(ctx: Context, alarm_id: str, hours: int = 24) -> str:
    """List state-change history for a specific alarm.

    alarm_id: OCID of the alarm
    hours:    how far back to look (default 24)
    """
    try:
        from datetime import timezone
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours)
        history = auth_manager.monitoring_client.get_alarm_history(
            alarm_id=alarm_id,
            timestamp_greater_than_or_equal_to=start_time.isoformat(),
        ).data
        entries = getattr(history, "entries", [])
        return json.dumps([
            {
                "timestamp": str(getattr(e, "timestamp", None)),
                "summary": getattr(e, "summary", None),
                "alarm_summary": getattr(e, "alarm_summary", None),
            }
            for e in entries
        ], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_log_groups(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI Logging log groups.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                groups = auth_manager.logging_client.list_log_groups(
                    compartment_id=cid).data
                results.extend([
                    {
                        "id": g.id,
                        "name": g.display_name,
                        "compartment_id": cid,
                        "time_created": str(g.time_created),
                    }
                    for g in groups
                ])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_logs(ctx: Context, log_group_id: str) -> str:
    """List individual logs within a log group.

    log_group_id: OCID of the log group
    """
    try:
        logs = auth_manager.logging_client.list_logs(log_group_id=log_group_id).data
        return json.dumps([
            {
                "id": l.id,
                "name": l.display_name,
                "log_type": l.log_type,
                "is_enabled": l.is_enabled,
                "lifecycle_state": l.lifecycle_state,
            }
            for l in logs
        ], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def search_logs(
    ctx: Context,
    query: str,
    time_start: str,
    time_end: str,
    limit: int = 100,
) -> str:
    """Search OCI Logging using the OCI Logging Query Language.

    query:      OCI Logging search expression.
                Example: 'search "ocid1.compartment.../logGroupId/logId" | where body like "ERROR"'
    time_start / time_end: ISO-8601 datetime strings
    limit:      max results to return (default 100)
    """
    try:
        details = oci.loggingsearch.models.SearchLogsDetails(
            time_start=time_start,
            time_end=time_end,
            search_query=query,
            is_return_field_info=False,
        )
        resp = auth_manager.log_search_client.search_logs(
            search_logs_details=details,
            limit=limit,
        ).data
        results = getattr(resp, "results", [])
        return json.dumps([
            {
                "time": str(getattr(r, "time", None)),
                "log_content": getattr(r, "log_content", None),
            }
            for r in results
        ], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== TOOL 32-33: NETWORK SECURITY GROUPS & LOAD BALANCERS ======================
@mcp.tool()
@tracked
async def list_network_security_groups(ctx: Context) -> str:
    """List Network Security Groups (NSGs)."""
    try:
        nsgs = auth_manager.network_client.list_network_security_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": n.id, "name": n.display_name, "state": n.lifecycle_state} for n in nsgs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_load_balancers(ctx: Context) -> str:
    """List Load Balancers."""
    try:
        lbs = auth_manager.lb_client.list_load_balancers(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": lb.id, "name": lb.display_name, "shape": lb.shape_name, "state": lb.lifecycle_state} for lb in lbs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 27-28: VAULT ======================
@mcp.tool()
@tracked
async def list_vaults(ctx: Context) -> str:
    """List Vaults."""
    try:
        vaults = auth_manager.vault_client.list_vaults(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name} for v in vaults], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async def list_secrets(ctx: Context) -> str:
    """List Secrets (Vault)."""
    try:
        secrets = auth_manager.vault_client.list_secrets(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": s.id, "name": s.secret_name} for s in secrets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== COMPUTE EXTENDED (3 new) ======================
@mcp.tool()
@tracked
async def list_images(
    ctx: Context,
    os_name: Optional[str] = None,
    compartment_scope: str = "single",
) -> str:
    """List available Compute OS images.

    os_name: optional filter e.g. 'Oracle Linux', 'Windows'
    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        seen, results = set(), []
        for cid in compartment_ids:
            try:
                kwargs = {"compartment_id": cid}
                if os_name:
                    kwargs["operating_system"] = os_name
                images = auth_manager.compute_client.list_images(**kwargs).data
                for i in images:
                    if i.id not in seen:
                        seen.add(i.id)
                        results.append({
                            "id": i.id, "name": i.display_name,
                            "os": i.operating_system,
                            "os_version": i.operating_system_version,
                            "state": i.lifecycle_state,
                        })
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_boot_volumes(ctx: Context, compartment_scope: str = "single") -> str:
    """List Boot Volumes.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                ads = auth_manager.identity_client.list_availability_domains(
                    compartment_id=cid).data
                for ad in ads:
                    vols = auth_manager.blockstorage_client.list_boot_volumes(
                        availability_domain=ad.name, compartment_id=cid).data
                    results.extend([{
                        "id": v.id, "name": v.display_name,
                        "size_gb": v.size_in_gbs, "state": v.lifecycle_state,
                        "ad": ad.name, "compartment_id": cid,
                    } for v in vols])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_instance_configurations(ctx: Context, compartment_scope: str = "single") -> str:
    """List Compute Instance Configurations (used in instance pools and autoscaling).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                configs = auth_manager.compute_mgmt_client.list_instance_configurations(
                    compartment_id=cid).data
                results.extend([{
                    "id": c.id, "name": c.display_name,
                    "time_created": str(c.time_created), "compartment_id": cid,
                } for c in configs])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== NETWORKING EXTENDED (4 new) ======================
@mcp.tool()
@tracked
async def list_internet_gateways(ctx: Context, compartment_scope: str = "single") -> str:
    """List Internet Gateways across VCNs.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                igs = auth_manager.network_client.list_internet_gateways(
                    compartment_id=cid).data
                results.extend([{
                    "id": ig.id, "name": ig.display_name,
                    "vcn_id": ig.vcn_id, "enabled": ig.is_enabled,
                    "state": ig.lifecycle_state, "compartment_id": cid,
                } for ig in igs])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_nat_gateways(ctx: Context, compartment_scope: str = "single") -> str:
    """List NAT Gateways.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                nats = auth_manager.network_client.list_nat_gateways(
                    compartment_id=cid).data
                results.extend([{
                    "id": n.id, "name": n.display_name,
                    "vcn_id": n.vcn_id, "public_ip": n.nat_ip,
                    "blocked": n.block_traffic, "state": n.lifecycle_state,
                    "compartment_id": cid,
                } for n in nats])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_service_gateways(ctx: Context, compartment_scope: str = "single") -> str:
    """List Service Gateways (Oracle Services Network access).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                sgs = auth_manager.network_client.list_service_gateways(
                    compartment_id=cid).data
                results.extend([{
                    "id": sg.id, "name": sg.display_name,
                    "vcn_id": sg.vcn_id, "state": sg.lifecycle_state,
                    "services": [s.service_name for s in sg.services],
                    "compartment_id": cid,
                } for sg in sgs])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_drgs(ctx: Context, compartment_scope: str = "single") -> str:
    """List Dynamic Routing Gateways (DRGs) for VPN / FastConnect / peering.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                drgs = auth_manager.network_client.list_drgs(compartment_id=cid).data
                results.extend([{
                    "id": d.id, "name": d.display_name,
                    "state": d.lifecycle_state, "compartment_id": cid,
                } for d in drgs])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== CONTAINER & OKE (4 new) ======================
@mcp.tool()
@tracked
async def list_clusters(ctx: Context, compartment_scope: str = "single") -> str:
    """List OKE (Oracle Kubernetes Engine) clusters.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                clusters = auth_manager.container_engine_client.list_clusters(
                    compartment_id=cid).data
                results.extend([{
                    "id": c.id, "name": c.name,
                    "k8s_version": c.kubernetes_version,
                    "state": c.lifecycle_state,
                    "vcn_id": c.vcn_id, "compartment_id": cid,
                } for c in clusters])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_node_pools(
    ctx: Context,
    cluster_id: Optional[str] = None,
    compartment_scope: str = "single",
) -> str:
    """List OKE Node Pools.

    cluster_id: optional — filter to a specific cluster
    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                kwargs = {"compartment_id": cid}
                if cluster_id:
                    kwargs["cluster_id"] = cluster_id
                pools = auth_manager.container_engine_client.list_node_pools(**kwargs).data
                results.extend([{
                    "id": p.id, "name": p.name,
                    "cluster_id": p.cluster_id,
                    "k8s_version": p.kubernetes_version,
                    "node_shape": p.node_shape,
                    "node_count": getattr(p, "node_config_details", {}).get("size") if hasattr(p, "node_config_details") and p.node_config_details else None,
                    "state": p.lifecycle_state, "compartment_id": cid,
                } for p in pools])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_container_instances(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI Container Instances (serverless containers, no Kubernetes).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                instances = auth_manager.container_instances_client.list_container_instances(
                    compartment_id=cid).data
                results.extend([{
                    "id": i.id, "name": i.display_name,
                    "state": i.lifecycle_state,
                    "shape": i.shape,
                    "container_count": i.container_count,
                    "compartment_id": cid,
                } for i in instances.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_container_repos(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCIR (Oracle Container Image Registry) repositories.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                repos = auth_manager.artifacts_client.list_container_repositories(
                    compartment_id=cid).data
                results.extend([{
                    "id": r.id, "name": r.display_name,
                    "image_count": r.image_count,
                    "is_public": r.is_public,
                    "state": r.lifecycle_state, "compartment_id": cid,
                } for r in repos.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== FUNCTIONS (3 new) ======================
@mcp.tool()
@tracked
async def list_applications(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI Functions applications (logical groupings of functions).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                apps = auth_manager.functions_client.list_applications(
                    compartment_id=cid).data
                results.extend([{
                    "id": a.id, "name": a.display_name,
                    "state": a.lifecycle_state,
                    "subnet_ids": a.subnet_ids, "compartment_id": cid,
                } for a in apps.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_functions(ctx: Context, application_id: str) -> str:
    """List all functions within a specific Functions application.

    application_id: OCID of the Functions application
    """
    try:
        fns = auth_manager.functions_client.list_functions(
            application_id=application_id).data
        return json.dumps([{
            "id": f.id, "name": f.display_name,
            "image": f.image,
            "memory_mb": f.memory_in_mbs,
            "timeout_s": f.timeout_in_seconds,
            "invoke_endpoint": f.invoke_endpoint,
            "state": f.lifecycle_state,
        } for f in fns.items], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def get_function(ctx: Context, function_id: str) -> str:
    """Get details of a specific OCI Function including invoke endpoint and config.

    function_id: OCID of the function
    """
    try:
        f = auth_manager.functions_client.get_function(function_id=function_id).data
        return json.dumps({
            "id": f.id, "name": f.display_name,
            "application_id": f.application_id,
            "image": f.image, "image_digest": f.image_digest,
            "memory_mb": f.memory_in_mbs, "timeout_s": f.timeout_in_seconds,
            "invoke_endpoint": f.invoke_endpoint,
            "config": f.config, "state": f.lifecycle_state,
        }, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== EVENTS & NOTIFICATIONS (3 new) ======================
@mcp.tool()
@tracked
async def list_event_rules(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI Events rules (triggers on resource state changes).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                rules = auth_manager.events_client.list_rules(compartment_id=cid).data
                results.extend([{
                    "id": r.id, "name": r.display_name,
                    "is_enabled": r.is_enabled,
                    "condition": r.condition,
                    "state": r.lifecycle_state, "compartment_id": cid,
                } for r in rules.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_notification_topics(ctx: Context, compartment_scope: str = "single") -> str:
    """List ONS (Oracle Notification Service) topics.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                topics = auth_manager.ons_cp_client.list_topics(
                    compartment_id=cid).data
                results.extend([{
                    "id": t.topic_id, "name": t.name,
                    "description": t.description,
                    "api_endpoint": t.api_endpoint,
                    "state": t.lifecycle_state, "compartment_id": cid,
                } for t in topics])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_notification_subscriptions(
    ctx: Context,
    topic_id: Optional[str] = None,
    compartment_scope: str = "single",
) -> str:
    """List ONS subscriptions (email, HTTPS, Slack, PagerDuty, Functions, etc.).

    topic_id: optional — filter to a specific topic
    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                kwargs = {"compartment_id": cid}
                if topic_id:
                    kwargs["topic_id"] = topic_id
                subs = auth_manager.ons_dp_client.list_subscriptions(**kwargs).data
                results.extend([{
                    "id": s.id,
                    "topic_id": s.topic_id,
                    "protocol": s.protocol,
                    "endpoint": s.endpoint,
                    "state": s.lifecycle_state, "compartment_id": cid,
                } for s in subs.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== DNS (3 new) ======================
@mcp.tool()
@tracked
async def list_dns_zones(ctx: Context, compartment_scope: str = "single") -> str:
    """List DNS zones (public and private).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                zones = auth_manager.dns_client.list_zones(compartment_id=cid).data
                results.extend([{
                    "id": z.id, "name": z.name,
                    "zone_type": z.zone_type,
                    "serial": z.serial,
                    "state": z.lifecycle_state, "compartment_id": cid,
                } for z in zones.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_dns_zone_records(ctx: Context, zone_name_or_id: str) -> str:
    """List all DNS records in a zone.

    zone_name_or_id: zone name (e.g. 'example.com') or OCID
    """
    try:
        records = auth_manager.dns_client.get_zone_records(
            zone_name_or_id=zone_name_or_id).data
        return json.dumps([{
            "domain": r.domain,
            "rtype": r.rtype,
            "ttl": r.ttl,
            "rdata": r.rdata,
        } for r in records.items], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_steering_policies(ctx: Context, compartment_scope: str = "single") -> str:
    """List DNS Traffic Management steering policies.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                policies = auth_manager.dns_client.list_steering_policies(
                    compartment_id=cid).data
                results.extend([{
                    "id": p.id, "name": p.display_name,
                    "template": p.template,
                    "state": p.lifecycle_state, "compartment_id": cid,
                } for p in policies.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== BUDGETS (2 new) ======================
@mcp.tool()
@tracked
async def list_budgets(ctx: Context) -> str:
    """List all cost budgets defined in the tenancy.

    Note: budgets are always scoped to the tenancy root (not sub-compartments).
    """
    try:
        tenancy_id = getattr(auth_manager.signer, "tenancy_id",
                             auth_manager.compartment_id)
        budgets = auth_manager.budget_client.list_budgets(
            compartment_id=tenancy_id).data
        return json.dumps([{
            "id": b.id, "name": b.display_name,
            "amount": b.amount,
            "reset_period": b.reset_period,
            "actual_spend": getattr(b, "actual_spend", None),
            "forecasted_spend": getattr(b, "forecasted_spend", None),
            "alert_rule_count": b.alert_rule_count,
            "state": b.lifecycle_state,
        } for b in budgets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def get_budget(ctx: Context, budget_id: str) -> str:
    """Get details of a specific budget including alert rules and spend.

    budget_id: OCID of the budget
    """
    try:
        b = auth_manager.budget_client.get_budget(budget_id=budget_id).data
        alert_rules = auth_manager.budget_client.list_alert_rules(
            budget_id=budget_id).data
        return json.dumps({
            "id": b.id, "name": b.display_name,
            "amount": b.amount, "reset_period": b.reset_period,
            "actual_spend": getattr(b, "actual_spend", None),
            "forecasted_spend": getattr(b, "forecasted_spend", None),
            "time_spend_computed": str(getattr(b, "time_spend_computed", None)),
            "state": b.lifecycle_state,
            "alert_rules": [{
                "id": r.id, "type": r.type,
                "threshold": r.threshold, "threshold_type": r.threshold_type,
                "message": r.message,
            } for r in alert_rules],
        }, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== AUDIT (1 new) ======================
@mcp.tool()
@tracked
async def list_audit_events(
    ctx: Context,
    time_start: str,
    time_end: str,
    limit: int = 100,
) -> str:
    """List OCI Audit events (who did what, when, from where).

    time_start / time_end: ISO-8601 datetime strings (e.g. '2026-02-01T00:00:00Z')
    limit: max results (default 100)

    Useful for: security investigations, compliance, change tracking.
    """
    try:
        events = auth_manager.audit_client.list_events(
            compartment_id=auth_manager.compartment_id,
            start_time=time_start,
            end_time=time_end,
        ).data
        return json.dumps([{
            "event_id": e.event_id,
            "event_type": e.event_type,
            "event_time": str(e.event_time),
            "principal_name": getattr(e.data, "principal_name", None) if e.data else None,
            "ip_address": getattr(e.data, "caller_ip_address", None) if e.data else None,
            "resource_name": getattr(e.data, "resource_name", None) if e.data else None,
            "resource_id": getattr(e.data, "resource_id", None) if e.data else None,
            "compartment_name": getattr(e.data, "compartment_name", None) if e.data else None,
        } for e in events[:limit]], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== API GATEWAY (2 new) ======================
@mcp.tool()
@tracked
async def list_api_gateways(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI API Gateways.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                gateways = auth_manager.api_gw_client.list_gateways(
                    compartment_id=cid).data
                results.extend([{
                    "id": g.id, "name": g.display_name,
                    "endpoint_type": g.endpoint_type,
                    "hostname": g.hostname,
                    "state": g.lifecycle_state, "compartment_id": cid,
                } for g in gateways.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_api_deployments(ctx: Context, compartment_scope: str = "single") -> str:
    """List API Gateway deployments (deployed API specs).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                deployments = auth_manager.api_deploy_client.list_deployments(
                    compartment_id=cid).data
                results.extend([{
                    "id": d.id, "name": d.display_name,
                    "gateway_id": d.gateway_id,
                    "path_prefix": d.path_prefix,
                    "endpoint": d.endpoint,
                    "state": d.lifecycle_state, "compartment_id": cid,
                } for d in deployments.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== BASTION (2 new) ======================
@mcp.tool()
@tracked
async def list_bastions(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI Bastion services (secure access to private resources).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                bastions = auth_manager.bastion_client.list_bastions(
                    compartment_id=cid).data
                results.extend([{
                    "id": b.id, "name": b.name,
                    "bastion_type": b.bastion_type,
                    "target_vcn_id": b.target_vcn_id,
                    "target_subnet_id": b.target_subnet_id,
                    "state": b.lifecycle_state, "compartment_id": cid,
                } for b in bastions])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_bastion_sessions(ctx: Context, bastion_id: str) -> str:
    """List active and recent sessions for a specific Bastion.

    bastion_id: OCID of the bastion
    """
    try:
        sessions = auth_manager.bastion_client.list_sessions(
            bastion_id=bastion_id).data
        return json.dumps([{
            "id": s.id, "name": s.display_name,
            "session_type": s.session_type,
            "target_resource_details": {
                "target_resource_id": getattr(s.target_resource_details, "target_resource_id", None),
                "target_resource_private_ip_address": getattr(s.target_resource_details, "target_resource_private_ip_address", None),
                "target_resource_port": getattr(s.target_resource_details, "target_resource_port", None),
            } if s.target_resource_details else None,
            "state": s.lifecycle_state,
            "time_created": str(s.time_created),
            "time_ttl_expires": str(getattr(s, "time_ttl_expires", None)),
        } for s in sessions], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== MYSQL & NOSQL (2 new) ======================
@mcp.tool()
@tracked
async def list_mysql_db_systems(ctx: Context, compartment_scope: str = "single") -> str:
    """List MySQL Database Systems.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                dbs = auth_manager.mysql_client.list_db_systems(
                    compartment_id=cid).data
                results.extend([{
                    "id": db.id, "name": db.display_name,
                    "mysql_version": db.mysql_version,
                    "shape_name": db.shape_name,
                    "state": db.lifecycle_state, "compartment_id": cid,
                } for db in dbs])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@mcp.tool()
@tracked
async def list_nosql_tables(ctx: Context, compartment_scope: str = "single") -> str:
    """List NoSQL Database tables.

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                tables = auth_manager.nosql_client.list_tables(
                    compartment_id=cid).data
                results.extend([{
                    "id": t.id, "name": t.name,
                    "state": t.lifecycle_state, "compartment_id": cid,
                } for t in tables.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== DEVOPS (1 new) ======================
@mcp.tool()
@tracked
async def list_devops_projects(ctx: Context, compartment_scope: str = "single") -> str:
    """List OCI DevOps projects (CI/CD pipelines, repos, deployments).

    compartment_scope: 'single' (default) | 'recursive' | 'tenancy'
    """
    try:
        compartment_ids = await resolve_compartment_ids(compartment_scope)
        results = []
        for cid in compartment_ids:
            try:
                projects = auth_manager.devops_client.list_projects(
                    compartment_id=cid).data
                results.extend([{
                    "id": p.id, "name": p.name,
                    "description": p.description,
                    "namespace": p.namespace,
                    "state": p.lifecycle_state, "compartment_id": cid,
                } for p in projects.items])
            except Exception:
                pass
        return json.dumps(results, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ====================== TOOL: TELEMETRY SUMMARY ======================
@mcp.tool()
@tracked
async def get_metrics_summary(ctx: Context) -> str:
    """Return aggregated usage stats from the local metrics file.

    Shows total calls, error rates, and avg latency per tool — sorted by popularity.
    Useful for understanding which tools are used most and which need improvement.
    Set OCI_MCP_TELEMETRY=off to disable all tracking.
    """
    if not os.path.exists(_METRICS_FILE):
        return json.dumps({
            "message": "No metrics recorded yet.",
            "telemetry": _TELEMETRY_MODE,
            "hint": "Call any tool first, then re-run get_metrics_summary.",
        })
    try:
        stats: dict = {}
        total = 0
        errors_total = 0
        with open(_METRICS_FILE) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    t = e.get("tool", "unknown")
                    if t not in stats:
                        stats[t] = {"calls": 0, "errors": 0, "total_ms": 0.0}
                    stats[t]["calls"] += 1
                    if not e.get("ok", True):
                        stats[t]["errors"] += 1
                        errors_total += 1
                    stats[t]["total_ms"] += e.get("ms", 0)
                    total += 1
                except Exception:
                    pass

        tools_summary = [
            {
                "tool":       t,
                "calls":      s["calls"],
                "errors":     s["errors"],
                "error_pct":  round(s["errors"] / s["calls"] * 100, 1) if s["calls"] else 0,
                "avg_ms":     round(s["total_ms"] / s["calls"]) if s["calls"] else 0,
            }
            for t, s in sorted(stats.items(), key=lambda x: -x[1]["calls"])
        ]

        return json.dumps({
            "telemetry":    _TELEMETRY_MODE,
            "metrics_file": _METRICS_FILE,
            "total_calls":  total,
            "total_errors": errors_total,
            "unique_tools": len(stats),
            "tools":        tools_summary,
        }, default=str)
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
    return {"message": "Oracle Context MCP Server v2.5", "tools": 69, "endpoint": "/mcp"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools_count": 69, "iam": "InstancePrincipal" if auth_manager.using_instance_principal else "Config"}


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
        logger.info("Starting Oracle Context MCP Server (HTTP) with 69 tools on {}:{}", host, port)
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
