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
  description = "Full OCIR image URL"
}
