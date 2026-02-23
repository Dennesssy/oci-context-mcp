# OCI Context MCP Server — Technical Specification

---

## F1 · STDIO Transport + Claude Desktop Config

### Architecture Decision
MCP supports two transports: **HTTP/SSE** (current) and **STDIO** (process pipe). Claude Desktop, Cursor, and VS Code require STDIO. The server must support both without a rewrite — FastMCP handles this via a `transport` flag.

### Entry Point Refactor

```python
# pyproject.toml
[project.scripts]
oci-mcp = "mcp_server:cli"

[project]
name = "oci-context-mcp"
version = "2.1.0"
```

```python
# mcp_server.py — bottom of file
import click

@click.command()
@click.option("--transport", default="http", type=click.Choice(["http", "stdio"]))
@click.option("--port", default=8000)
@click.option("--host", default="0.0.0.0")
@click.option("--print-config", is_flag=True)
def cli(transport, port, host, print_config):
    if print_config:
        _print_claude_config(port)
        return
    if transport == "stdio":
        mcp.run()                          # FastMCP STDIO mode
    else:
        uvicorn.run(app, host=host, port=port)

def _print_claude_config(port: int):
    import json, sys
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
```

### Claude Desktop Config Output
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

### Key Constraint
In STDIO mode, all `print()` / `logger` output to stdout **breaks the MCP wire protocol**. Redirect loguru to stderr only:
```python
logger.remove()
logger.add(sys.stderr, level="WARNING")   # STDIO mode
logger.add("oci_mcp_server.log", rotation="10 MB")  # always
```

---

## F2 · IAM Compartment Tree Traversal

### Data Model
```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class CompartmentNode:
    id: str
    name: str
    lifecycle_state: str
    children: list["CompartmentNode"] = field(default_factory=list)
    depth: int = 0
```

### Recursive Builder with Cache
```python
import asyncio
from functools import lru_cache
from datetime import datetime, timedelta

_tree_cache: dict = {}   # {root_id: (tree, expires_at)}

async def build_compartment_tree(root_id: str, max_depth: int = 5) -> CompartmentNode:
    cached = _tree_cache.get(root_id)
    if cached and cached[1] > datetime.utcnow():
        return cached[0]

    async def _fetch(parent_id: str, depth: int) -> list[CompartmentNode]:
        if depth >= max_depth:
            return []
        resp = auth_manager.identity_client.list_compartments(
            compartment_id=parent_id,
            lifecycle_state="ACTIVE"
        ).data
        nodes = []
        tasks = []
        for c in resp:
            node = CompartmentNode(id=c.id, name=c.name,
                                   lifecycle_state=c.lifecycle_state, depth=depth)
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

def flatten_compartment_ids(node: CompartmentNode) -> list[str]:
    ids = [node.id]
    for child in node.children:
        ids.extend(flatten_compartment_ids(child))
    return ids
```

### Tool Integration Pattern
```python
@mcp.tool()
async def list_compute_instances(
    ctx: Context,
    limit: int = 100,
    compartment_scope: str = "single"   # single | recursive | tenancy
) -> str:
    root = auth_manager.compartment_id
    if compartment_scope == "tenancy":
        root = getattr(auth_manager.signer, "tenancy_id", root)

    if compartment_scope == "single":
        compartment_ids = [root]
    else:
        tree = await build_compartment_tree(root)
        compartment_ids = flatten_compartment_ids(tree)

    results = []
    for cid in compartment_ids:
        try:
            resp = auth_manager.compute_client.list_instances(
                compartment_id=cid, limit=limit).data
            results.extend([{"id": i.id, "name": i.display_name,
                             "shape": i.shape, "state": i.lifecycle_state,
                             "compartment_id": cid} for i in resp])
        except ServiceError:
            pass  # compartment may not have compute access
    return json.dumps(results, default=str)
```

---

## F3 · Monitoring Queries (Metrics + Logs)

### New Clients
```python
# Add to OCIAuthManager.__init__
self.monitoring_client = oci.monitoring.MonitoringClient({}, signer=self.signer)
self.logging_client    = oci.logging.LoggingManagementClient({}, signer=self.signer)
self.log_search_client = oci.log_analytics.LogAnalyticsClient({}, signer=self.signer)
```

### Metrics Query
```python
@mcp.tool()
async def query_metrics(
    ctx: Context,
    namespace: str,            # e.g. "oci_computeagent"
    metric_name: str,          # e.g. "CpuUtilization"
    start: str,                # ISO datetime
    end: str,
    interval: str = "PT5M",   # ISO 8601 duration
    statistic: str = "mean",  # mean | max | min | sum | count
    resource_id: Optional[str] = None
) -> str:
    query = f"{metric_name}[{interval}]{{{statistic}}}"
    if resource_id:
        query = f"{metric_name}[{interval}]{{resourceId = \"{resource_id}\"}}.{statistic}()"
    details = oci.monitoring.models.SummarizeMetricsDataDetails(
        namespace=namespace,
        query=query,
        start_time=start,
        end_time=end
    )
    resp = auth_manager.monitoring_client.summarize_metrics_data(
        compartment_id=auth_manager.compartment_id,
        summarize_metrics_data_details=details
    ).data
    return json.dumps([{
        "name": m.name,
        "namespace": m.namespace,
        "datapoints": [{"ts": str(d.timestamp), "value": d.value} for d in m.aggregated_datapoints]
    } for m in resp], default=str)
```

### Log Search
```python
@mcp.tool()
async def search_logs(
    ctx: Context,
    query: str,                # OCI Logging Query Language expression
    time_start: str,           # ISO datetime
    time_end: str,
    limit: int = 100
) -> str:
    # OCI Logging Search uses its own query language:
    # e.g. "search \"compartmentId/logGroupId/logId\" | where body like 'ERROR'"
    details = oci.log_analytics.models.SearchLogsDetails(
        time_start=time_start,
        time_end=time_end,
        search_query=query,
        is_return_field_info=False
    )
    resp = auth_manager.log_search_client.search_logs(
        search_logs_details=details,
        limit=limit
    ).data
    return json.dumps([{
        "time": str(r.time),
        "source": getattr(r, "source", None),
        "subject": getattr(r, "subject", None),
        "data": getattr(r, "data", None)
    } for r in resp.results], default=str)
```

---

## F4 · Full CRUD with Confirmation/Rollback

### OperationPlan Framework
```python
from dataclasses import dataclass, field
from typing import Callable, Optional, Any
import uuid, time

@dataclass
class OperationPlan:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    action: str = ""              # "create" | "update" | "delete"
    resource_type: str = ""
    resource_id: Optional[str] = None
    payload: dict = field(default_factory=dict)
    reversible: bool = False
    rollback_fn: Optional[Callable] = None
    snapshot: Optional[dict] = None
    created_at: float = field(default_factory=time.time)

class RollbackRegistry:
    _ops: dict[str, OperationPlan] = {}
    TTL = 1800  # 30 minutes

    @classmethod
    def register(cls, plan: OperationPlan):
        cls._ops[plan.id] = plan
        cls._purge()

    @classmethod
    def rollback(cls, op_id: str) -> str:
        plan = cls._ops.get(op_id)
        if not plan:
            raise ValueError(f"Operation {op_id} not found or expired")
        if not plan.reversible or not plan.rollback_fn:
            raise ValueError(f"Operation {op_id} is not reversible")
        plan.rollback_fn()
        del cls._ops[op_id]
        return f"Rolled back {plan.action} on {plan.resource_type} {plan.resource_id}"

    @classmethod
    def _purge(cls):
        cutoff = time.time() - cls.TTL
        cls._ops = {k: v for k, v in cls._ops.items() if v.created_at > cutoff}
```

### Write Tool Pattern
```python
@mcp.tool()
async def create_bucket(
    ctx: Context,
    bucket_name: str,
    storage_tier: str = "Standard",
    confirm: bool = False,
    dry_run: bool = False
) -> str:
    ns = auth_manager.os_client.get_namespace().data
    plan = OperationPlan(
        action="create",
        resource_type="bucket",
        resource_id=bucket_name,
        payload={"namespace": ns, "name": bucket_name, "storage_tier": storage_tier},
        reversible=True,
        rollback_fn=lambda: auth_manager.os_client.delete_bucket(
            namespace_name=ns, bucket_name=bucket_name)
    )
    if dry_run or not confirm:
        return json.dumps({
            "plan": {"action": plan.action, "resource": plan.resource_type,
                     "name": bucket_name, "tier": storage_tier},
            "requires_confirm": not confirm,
            "hint": "Re-call with confirm=True to execute"
        })
    details = oci.object_storage.models.CreateBucketDetails(
        name=bucket_name, compartment_id=auth_manager.compartment_id,
        storage_tier=storage_tier)
    auth_manager.os_client.create_bucket(namespace_name=ns, create_bucket_details=details)
    RollbackRegistry.register(plan)
    return json.dumps({"created": bucket_name, "rollback_id": plan.id,
                       "rollback_window": "30 minutes"})

@mcp.tool()
async def rollback_operation(ctx: Context, operation_id: str) -> str:
    """Reverse a previously executed write operation within the 30-minute window."""
    return RollbackRegistry.rollback(operation_id)
```

---

## F5 · Multi-Region Query Routing

### Regional Client Pool
```python
import asyncio
from typing import TypeVar

class RegionalClientPool:
    _pools: dict[str, "OCIAuthManager"] = {}

    @classmethod
    def get(cls, region: str) -> "OCIAuthManager":
        if region not in cls._pools:
            mgr = OCIAuthManager.__new__(OCIAuthManager)
            mgr.__init__(region_override=region)
            cls._pools[region] = mgr
        return cls._pools[region]

    @classmethod
    async def fan_out(cls, fn_name: str, regions: list[str], **kwargs) -> list[dict]:
        sem = asyncio.Semaphore(5)  # max 5 concurrent region calls

        async def _call(region):
            async with sem:
                mgr = cls.get(region)
                try:
                    result = getattr(mgr, fn_name)(**kwargs)
                    return {"region": region, "data": result, "error": None}
                except Exception as e:
                    return {"region": region, "data": [], "error": str(e)}

        return await asyncio.gather(*[_call(r) for r in regions])
```

### OCIAuthManager Region Override
```python
# Modified __init__ signature
def __init__(self, region_override: Optional[str] = None):
    self.region = region_override or os.getenv("OCI_REGION", "us-phoenix-1")
    # ... rest of init unchanged, region injected into clients via config dict
    config_dict = {"region": self.region}
    self.compute_client = oci.core.ComputeClient(config_dict, signer=self.signer)
    # etc.
```

### Multi-Region Tool Pattern
```python
@mcp.tool()
async def list_compute_instances(
    ctx: Context,
    limit: int = 100,
    region: str = "current",   # "current" | specific region | "all"
    compartment_scope: str = "single"
) -> str:
    if region == "current":
        regions = [auth_manager.region]
    elif region == "all":
        all_regions = auth_manager.identity_client.list_regions().data
        regions = [r.name for r in all_regions]
    else:
        regions = [region]

    results = await RegionalClientPool.fan_out("_list_instances_raw",
                                               regions, limit=limit)
    return json.dumps(results, default=str)
```

---

## F6 · Extensions SDK for Custom Tools

### Plugin Base Class (published as `oci-mcp-sdk`)
```python
# oci_mcp_sdk/plugin.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolSchema:
    name: str
    description: str
    parameters: dict   # JSON Schema object

class OCIToolPlugin(ABC):
    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        pass

    @abstractmethod
    async def execute(self, ctx: Any, auth: Any, **kwargs) -> str:
        pass

    @property
    def required_clients(self) -> list[str]:
        """Declare which auth_manager clients this plugin needs."""
        return []
```

### Example Plugin
```python
# ~/.oci-mcp/plugins/cost_anomaly.py
from oci_mcp_sdk import OCIToolPlugin, ToolSchema

class CostAnomalyDetector(OCIToolPlugin):
    @property
    def schema(self):
        return ToolSchema(
            name="detect_cost_anomalies",
            description="Flag services with >20% cost spike vs prior period",
            parameters={"type": "object", "properties": {
                "days": {"type": "integer", "default": 7}
            }}
        )

    @property
    def required_clients(self):
        return ["usage_client"]

    async def execute(self, ctx, auth, days=7, **kwargs) -> str:
        # ... custom logic using auth.usage_client
        return json.dumps(anomalies)

# Plugin registration
plugin = CostAnomalyDetector()
```

### Plugin Loader
```python
import importlib.util, pathlib, sys

def load_plugins(mcp_instance):
    plugin_dirs = [
        pathlib.Path.home() / ".oci-mcp" / "plugins",
        pathlib.Path("./plugins")
    ]
    for plugin_dir in plugin_dirs:
        if not plugin_dir.exists():
            continue
        for py_file in plugin_dir.glob("*.py"):
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            plugin: OCIToolPlugin = getattr(mod, "plugin", None)
            if plugin:
                # Dynamically register as MCP tool
                _register_plugin_tool(mcp_instance, plugin)
                logger.info(f"Loaded plugin: {plugin.schema.name}")

def _register_plugin_tool(mcp, plugin: OCIToolPlugin):
    async def _tool_fn(ctx, **kwargs):
        return await plugin.execute(ctx, auth_manager, **kwargs)
    _tool_fn.__name__ = plugin.schema.name
    _tool_fn.__doc__ = plugin.schema.description
    mcp.tool()(_tool_fn)
```

---

## F7 · OCI Console SSO Integration

### OAuth2 / OIDC Flow
OCI IAM Identity Domains expose a standard OIDC endpoint:
```
https://idcs-{tenant}.identity.oraclecloud.com/oauth2/v1/authorize
```

### FastAPI OAuth Routes
```python
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
from starlette.requests import Request
import secrets

config = Config(environ=os.environ)
oauth = OAuth(config)
oauth.register(
    name="oci",
    server_metadata_url=f"https://{os.getenv('OCI_IDCS_DOMAIN')}/.well-known/openid-configuration",
    client_id=os.getenv("OCI_OAUTH_CLIENT_ID"),
    client_secret=os.getenv("OCI_OAUTH_CLIENT_SECRET"),
    client_kwargs={"scope": "openid profile email"}
)

_sessions: dict[str, dict] = {}  # state → token (in-memory; use Redis in prod)

@app.get("/auth/login")
async def login(request: Request):
    state = secrets.token_urlsafe(16)
    redirect_uri = str(request.url_for("auth_callback"))
    return await oauth.oci.authorize_redirect(request, redirect_uri, state=state)

@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.oci.authorize_access_token(request)
    _sessions[token["access_token"][:16]] = token
    return {"access_token": token["access_token"], "expires_in": token.get("expires_in")}
```

### Token Injection into MCP Tools
```python
async def verify_iam_token(authorization: Optional[str] = Header(None)):
    if not authorization:
        return {"auth_mode": "InstancePrincipal"}
    bearer = authorization.removeprefix("Bearer ")
    session = _sessions.get(bearer[:16])
    if session:
        return {"auth_mode": "SSO", "user": session.get("userinfo", {}).get("email")}
    return {"auth_mode": "InstancePrincipal"}
```

### Required Environment Variables
```bash
OCI_IDCS_DOMAIN=idcs-abc123.identity.oraclecloud.com
OCI_OAUTH_CLIENT_ID=<app-client-id>
OCI_OAUTH_CLIENT_SECRET=<app-client-secret>
```

### Browser Launch (CLI mode)
```python
@click.option("--auth-mode", default="instance-principal",
              type=click.Choice(["instance-principal", "api-key", "browser"]))
def cli(auth_mode, ...):
    if auth_mode == "browser":
        import webbrowser, threading
        threading.Timer(1, lambda: webbrowser.open("http://localhost:8000/auth/login")).start()
    # start server normally
```

---

## Shared Patterns

### Error Hierarchy
```python
class OCIMCPError(Exception):
    pass

class AuthError(OCIMCPError):
    http_status = 401

class NotFoundError(OCIMCPError):
    http_status = 404

class ConfirmationRequired(OCIMCPError):
    http_status = 202   # Accepted, not executed

class RollbackExpired(OCIMCPError):
    http_status = 410   # Gone
```

### Response Envelope (consistent across all tools)
```python
def ok(data: Any, meta: dict = None) -> str:
    return json.dumps({
        "ok": True,
        "data": data,
        "meta": meta or {},
        "region": auth_manager.region
    }, default=str)

def err(message: str, code: int = 500) -> str:
    return json.dumps({"ok": False, "error": message, "code": code})
```

### Pagination Helper
```python
async def paginate(client_fn, **kwargs) -> list:
    results, page = [], None
    while True:
        if page:
            kwargs["page"] = page
        resp = client_fn(**kwargs)
        results.extend(resp.data)
        page = resp.headers.get("opc-next-page")
        if not page:
            break
    return results
```
