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
