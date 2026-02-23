output "mcp_server_url" {
  description = "MCP Server public endpoint (via API Gateway)"
  value       = "https://${oci_apigateway_gateway.mcp_gateway.hostname}/mcp"
}

output "mcp_server_health_url" {
  description = "MCP Server health check (via API Gateway)"
  value       = "https://${oci_apigateway_gateway.mcp_gateway.hostname}/health"
}

output "container_instance_private_ip" {
  description = "Container Instance private IP (for debugging; not publicly reachable)"
  value       = oci_container_instances_container_instance.mcp_instance.vnics[0].private_ip
}

output "container_instance_id" {
  description = "Container Instance OCID"
  value       = oci_container_instances_container_instance.mcp_instance.id
}

output "api_gateway_id" {
  description = "API Gateway OCID"
  value       = oci_apigateway_gateway.mcp_gateway.id
}

output "log_group_id" {
  description = "OCI Logging Log Group OCID — use in search_logs MCP tool"
  value       = oci_logging_log_group.mcp_log_group.id
}

output "nsg_id" {
  description = "NSG OCID attached to the Container Instance"
  value       = oci_core_network_security_group.mcp_nsg.id
}

output "dynamic_group_id" {
  description = "Dynamic Group OCID"
  value       = oci_identity_dynamic_group.mcp_dg.id
}

output "policy_id" {
  description = "IAM Policy OCID"
  value       = oci_identity_policy.mcp_policy.id
}
