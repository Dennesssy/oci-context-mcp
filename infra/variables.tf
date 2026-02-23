variable "tenancy_ocid" {
  description = "OCI Tenancy OCID"
}

variable "user_ocid" {
  description = "OCI User OCID"
}

variable "fingerprint" {
  description = "API Key Fingerprint"
}

variable "private_key_path" {
  description = "Path to private key file"
}

variable "region" {
  default     = "us-phoenix-1"
  description = "OCI Region"
}

variable "compartment_id" {
  description = "Compartment OCID for resources"
}

variable "vcn_id" {
  description = "VCN OCID"
}

variable "subnet_id" {
  description = "Subnet OCID"
}

variable "ocir_image_url" {
  description = "Full OCIR image URL (e.g. phx.ocir.io/<namespace>/<repo>:<tag>)"
}

variable "availability_domain" {
  description = "Availability Domain name for Container Instance (e.g. 'AD-1')"
  default     = "AD-1"
}

variable "api_gateway_subnet_id" {
  description = "Public subnet OCID for the API Gateway (separate from the private Container Instance subnet)"
}

variable "api_gateway_subnet_cidr" {
  description = "CIDR block of the API Gateway subnet — used to restrict NSG ingress to port 8000"
}

variable "mcp_server_port" {
  description = "Container port the MCP server listens on"
  default     = 8000
}

variable "alert_email" {
  description = "Email address to receive OCI Monitoring alarm notifications"
}
