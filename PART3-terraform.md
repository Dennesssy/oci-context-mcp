# PART 3: Main Terraform and Outputs

Create these 2 terraform files:

## 1. infra/main.tf (complete orchestration)

```hcl
# Dynamic Group for Instance Principal
resource "oci_identity_dynamic_group" "mcp_dg" {
  compartment_id = var.tenancy_ocid
  name           = "oci-context-mcp-dg"
  matching_rule  = "instance.compartment.id = '${var.compartment_id}'"
  description    = "Dynamic group for OCI Context MCP Container Instances"
}

# IAM Policy for Object Storage + Compute
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
  ]
  description = "Permissions for OCI Context MCP Server"
}

# Container Instance (OCI Compute)
resource "oci_container_instances_container_instance" "mcp_instance" {
  availability_domain = "AD-1"
  compartment_id      = var.compartment_id
  display_name        = "oci-context-mcp-server"
  shape               = "CI.Standard.E4.Flex"

  shape_config {
    ocpus         = 1
    memory_in_gbs = 4
  }

  containers {
    image_url      = var.ocir_image_url
    display_name   = "mcp-container"

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
    subnet_id = var.subnet_id
  }
}
```

## 2. infra/outputs.tf

```hcl
output "mcp_server_url" {
  description = "MCP Server endpoint URL"
  value       = "http://${oci_container_instances_container_instance.mcp_instance.vnics[0].private_ip}:8000/mcp"
}

output "mcp_server_health_url" {
  description = "Health check endpoint"
  value       = "http://${oci_container_instances_container_instance.mcp_instance.vnics[0].private_ip}:8000/health"
}

output "container_instance_id" {
  description = "Container Instance OCID"
  value       = oci_container_instances_container_instance.mcp_instance.id
}

output "dynamic_group_id" {
  description = "Dynamic Group OCID"
  value       = oci_identity_dynamic_group.mcp_dg.id
}

output "policy_id" {
  description = "IAM Policy OCID"
  value       = oci_identity_policy.mcp_policy.id
}
```

Done. These complete the Terraform infrastructure code.
