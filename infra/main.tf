# ─── A: Auth — Dynamic Group with correct Container Instance resource type ────
resource "oci_identity_dynamic_group" "mcp_dg" {
  compartment_id = var.tenancy_ocid
  name           = "oci-context-mcp-dg"
  # resource.type = 'computecontainerinstance' is required for Container Instances;
  # 'instance.compartment.id' alone matches only bare-metal/VM Compute instances.
  matching_rule = "Any {resource.type = 'computecontainerinstance', resource.compartment.id = '${var.compartment_id}'}"
  description   = "Dynamic group for OCI Context MCP Container Instances"
}

# ─── A: Auth — IAM Policy ─────────────────────────────────────────────────────
resource "oci_identity_policy" "mcp_policy" {
  compartment_id = var.compartment_id
  name           = "oci-context-mcp-policy"
  statements = [
    "Allow dynamic-group oci-context-mcp-dg to read compute-instances in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read compute-shapes in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read object-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read virtual-network-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read block-volumes in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read file-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to use resource-search in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read database-family in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read compartments in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read identity-users in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read groups in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read policies in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read vaults in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to read secrets in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to manage ons-topics in compartment id ${var.compartment_id}",
    "Allow dynamic-group oci-context-mcp-dg to manage logging-family in compartment id ${var.compartment_id}",
  ]
  description = "Permissions for OCI Context MCP Server"
}

# ─── C: Compute — NSG restricting ingress to port 8000 from API GW subnet ────
resource "oci_core_network_security_group" "mcp_nsg" {
  compartment_id = var.compartment_id
  vcn_id         = var.vcn_id
  display_name   = "oci-mcp-server-nsg"
}

resource "oci_core_network_security_group_security_rule" "mcp_ingress_8000" {
  network_security_group_id = oci_core_network_security_group.mcp_nsg.id
  direction                 = "INGRESS"
  protocol                  = "6" # TCP
  source                    = var.api_gateway_subnet_cidr
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = 8000
      max = 8000
    }
  }
  description = "Allow API Gateway subnet → MCP server port 8000 only"
}

resource "oci_core_network_security_group_security_rule" "mcp_egress_all" {
  network_security_group_id = oci_core_network_security_group.mcp_nsg.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  description               = "Allow all egress (OCI API calls)"
}

# ─── C: Compute — Container Instance (ARM64 free tier shape) ─────────────────
resource "oci_container_instances_container_instance" "mcp_instance" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_id
  display_name        = "oci-context-mcp-server"
  # CI.Standard.A1.Flex = ARM64, free-tier eligible (4 OCPUs / 24 GB total allowance)
  shape = "CI.Standard.A1.Flex"

  shape_config {
    ocpus         = 1
    memory_in_gbs = 6
  }

  containers {
    image_url    = var.ocir_image_url
    display_name = "mcp-container"

    environment_variables = {
      OCI_COMPARTMENT_ID = var.compartment_id
      OCI_REGION         = var.region
      LOG_LEVEL          = "INFO"
    }

    port_mappings {
      container_port = 8000
      protocol       = "TCP"
    }
  }

  vnics {
    subnet_id        = var.subnet_id
    assign_public_ip = false # private subnet only — access via API Gateway
    nsg_ids          = [oci_core_network_security_group.mcp_nsg.id]
  }
}

# ─── E: Edge — API Gateway (public) → Container Instance (private) ───────────
resource "oci_apigateway_gateway" "mcp_gateway" {
  compartment_id = var.compartment_id
  display_name   = "oci-mcp-server-gateway"
  endpoint_type  = "PUBLIC"
  subnet_id      = var.api_gateway_subnet_id
}

resource "oci_apigateway_deployment" "mcp_deployment" {
  compartment_id = var.compartment_id
  display_name   = "oci-mcp-server-v1"
  gateway_id     = oci_apigateway_gateway.mcp_gateway.id
  path_prefix    = "/"

  specification {
    routes {
      path    = "/{path*}"
      methods = ["ANY"]

      backend {
        type = "HTTP_BACKEND"
        # Container Instance private IP is a Terraform computed attribute —
        # resolved on first apply; no separate tfvars entry required.
        url = "http://${oci_container_instances_container_instance.mcp_instance.vnics[0].private_ip}:${var.mcp_server_port}/{path}"
      }

      request_policies {
        cors {
          allowed_origins              = ["https://${oci_apigateway_gateway.mcp_gateway.hostname}"]
          allowed_methods              = ["GET", "POST", "OPTIONS"]
          allowed_headers              = ["Content-Type", "Authorization"]
          exposed_headers              = []
          is_allow_credentials_include = false
          max_age_in_seconds           = 3600
        }
      }
    }
  }
}

# ─── M: Monitoring — Log Group + Custom Log ──────────────────────────────────
resource "oci_logging_log_group" "mcp_log_group" {
  compartment_id = var.compartment_id
  display_name   = "oci-mcp-server-logs"
  description    = "Log group for OCI Context MCP Server application logs"
}

resource "oci_logging_log" "mcp_app_log" {
  display_name = "mcp-application-log"
  log_group_id = oci_logging_log_group.mcp_log_group.id
  log_type     = "CUSTOM"

  configuration {
    compartment_id = var.compartment_id
  }

  is_enabled         = true
  retention_duration = 30
}

# ─── M: Monitoring — ONS topic + email subscription + latency alarm ──────────
resource "oci_ons_notification_topic" "mcp_alerts" {
  compartment_id = var.compartment_id
  name           = "oci-mcp-server-alerts"
  description    = "Alert topic for OCI Context MCP Server"
}

resource "oci_ons_subscription" "mcp_alert_email" {
  compartment_id = var.compartment_id
  topic_id       = oci_ons_notification_topic.mcp_alerts.id
  protocol       = "EMAIL"
  endpoint       = var.alert_email
}

resource "oci_monitoring_alarm" "mcp_gateway_5xx" {
  compartment_id        = var.compartment_id
  display_name          = "mcp-gateway-5xx-errors"
  destinations          = [oci_ons_notification_topic.mcp_alerts.id]
  metric_compartment_id = var.compartment_id
  namespace             = "oci_apigateway"
  query                 = "ResponseCount[5m]{httpResponseCode = \"5XX\"}.sum() > 10"
  severity              = "CRITICAL"
  is_enabled            = true
  pending_duration      = "PT5M"
  body                  = "MCP Server: >10 5XX responses in 5 minutes. Check logs in ${oci_logging_log_group.mcp_log_group.id}"
}
