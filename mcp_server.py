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
_SERVER_VERSION = "2.4.0"


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
async defserver_health(ctx: Context) -> str:
    """Health check and IAM status."""
    return json.dumps({
        "status": "healthy",
        "server": "Oracle Context MCP Server v2.4",
        "tools": 38,
        "iam_mode": "InstancePrincipal" if auth_manager.using_instance_principal else "Config",
        "region": auth_manager.region,
        "compartment_id": auth_manager.compartment_id
    })

@mcp.tool()
@tracked
async defget_tenancy_info(ctx: Context) -> str:
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
async deflist_regions(ctx: Context) -> str:
    """List all OCI regions."""
    try:
        regions = auth_manager.identity_client.list_regions().data
        return json.dumps([{"name": r.name, "key": r.key} for r in regions], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 4-6: COMPUTE ======================
@mcp.tool()
@tracked
async deflist_compute_instances(
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
async deflist_compute_shapes(ctx: Context) -> str:
    """List available Compute shapes."""
    try:
        shapes = auth_manager.compute_client.list_shapes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"shape": s.shape, "ocpus": s.ocpus, "memory_gb": s.memory_in_gbs} for s in shapes], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async defget_compute_instance(ctx: Context, instance_id: str) -> str:
    """Get details of a specific Compute instance."""
    try:
        inst = auth_manager.compute_client.get_instance(instance_id=instance_id).data
        return json.dumps({"id": inst.id, "name": inst.display_name, "shape": inst.shape, "state": inst.lifecycle_state}, default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 7-10: OBJECT STORAGE ======================
@mcp.tool()
@tracked
async defget_object_storage_namespace(ctx: Context) -> str:
    """Get tenancy Object Storage namespace."""
    try:
        ns = auth_manager.os_client.get_namespace().data
        return json.dumps({"namespace": ns})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_buckets(ctx: Context, compartment_scope: str = "single") -> str:
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
async deflist_objects(ctx: Context, bucket_name: str, limit: int = 100) -> str:
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
async defget_bucket_details(ctx: Context, bucket_name: str) -> str:
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
async deflist_compartments(ctx: Context, parent_id: Optional[str] = None) -> str:
    """List compartments."""
    try:
        cid = parent_id or auth_manager.compartment_id
        comps = auth_manager.identity_client.list_compartments(compartment_id=cid).data
        return json.dumps([{"id": c.id, "name": c.name} for c in comps], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_users(ctx: Context) -> str:
    """List IAM users."""
    try:
        users = auth_manager.identity_client.list_users(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": u.id, "name": u.name} for u in users], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_groups(ctx: Context) -> str:
    """List IAM groups."""
    try:
        groups = auth_manager.identity_client.list_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": g.id, "name": g.name} for g in groups], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_policies(ctx: Context) -> str:
    """List IAM policies."""
    try:
        policies = auth_manager.identity_client.list_policies(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": p.id, "name": p.name, "statements": p.statements} for p in policies], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 15-16: COMPARTMENT TREE (F2) ======================
@mcp.tool()
@tracked
async defget_compartment_tree(ctx: Context, max_depth: int = 5) -> str:
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
async defresolve_compartment_by_name(ctx: Context, name: str) -> str:
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
async deflist_vcns(ctx: Context, compartment_scope: str = "single") -> str:
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
async deflist_subnets(ctx: Context, vcn_id: Optional[str] = None) -> str:
    """List subnets (optionally filter by VCN)."""
    try:
        subnets = auth_manager.network_client.list_subnets(compartment_id=auth_manager.compartment_id, vcn_id=vcn_id).data
        return json.dumps([{"id": s.id, "name": s.display_name, "cidr": s.cidr_block} for s in subnets], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_security_lists(ctx: Context) -> str:
    """List Security Lists."""
    try:
        sls = auth_manager.network_client.list_security_lists(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": sl.id, "name": sl.display_name} for sl in sls], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_route_tables(ctx: Context) -> str:
    """List Route Tables."""
    try:
        rts = auth_manager.network_client.list_route_tables(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": rt.id, "name": rt.display_name} for rt in rts], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 19-20: BLOCK & FILE STORAGE ======================
@mcp.tool()
@tracked
async deflist_block_volumes(ctx: Context) -> str:
    """List Block Volumes."""
    try:
        vols = auth_manager.blockstorage_client.list_volumes(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name, "size_gb": v.size_in_gbs} for v in vols], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_file_systems(ctx: Context, availability_domain: Optional[str] = None) -> str:
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
async defsearch_resources(ctx: Context, query: str, limit: int = 50) -> str:
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
async deflist_autonomous_databases(ctx: Context, compartment_scope: str = "single") -> str:
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
async deflist_db_systems(ctx: Context, compartment_scope: str = "single") -> str:
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
async defget_usage_summary(ctx: Context, time_start: str, time_end: str) -> str:
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
async deflist_metric_namespaces(ctx: Context, compartment_scope: str = "single") -> str:
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
async defquery_metrics(
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
async deflist_alarms(
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
async defget_alarm_status(ctx: Context, compartment_scope: str = "single") -> str:
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
async deflist_alarm_history(ctx: Context, alarm_id: str, hours: int = 24) -> str:
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
async deflist_log_groups(ctx: Context, compartment_scope: str = "single") -> str:
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
async deflist_logs(ctx: Context, log_group_id: str) -> str:
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
async defsearch_logs(
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
async deflist_network_security_groups(ctx: Context) -> str:
    """List Network Security Groups (NSGs)."""
    try:
        nsgs = auth_manager.network_client.list_network_security_groups(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": n.id, "name": n.display_name, "state": n.lifecycle_state} for n in nsgs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_load_balancers(ctx: Context) -> str:
    """List Load Balancers."""
    try:
        lbs = auth_manager.lb_client.list_load_balancers(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": lb.id, "name": lb.display_name, "shape": lb.shape_name, "state": lb.lifecycle_state} for lb in lbs], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ====================== TOOL 27-28: VAULT ======================
@mcp.tool()
@tracked
async deflist_vaults(ctx: Context) -> str:
    """List Vaults."""
    try:
        vaults = auth_manager.vault_client.list_vaults(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": v.id, "name": v.display_name} for v in vaults], default=str)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@mcp.tool()
@tracked
async deflist_secrets(ctx: Context) -> str:
    """List Secrets (Vault)."""
    try:
        secrets = auth_manager.vault_client.list_secrets(compartment_id=auth_manager.compartment_id).data
        return json.dumps([{"id": s.id, "name": s.secret_name} for s in secrets], default=str)
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
    return {"message": "Oracle Context MCP Server v2.4", "tools": 38, "endpoint": "/mcp"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools_count": 38, "iam": "InstancePrincipal" if auth_manager.using_instance_principal else "Config"}


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
        logger.info("Starting Oracle Context MCP Server (HTTP) with 38 tools on {}:{}", host, port)
        uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
