# PART 2: Dockerfile, Requirements, Environment, and Terraform

Create these 5 files exactly:

## 1. requirements.txt

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
mcp[server]>=1.0.0
oci>=2.167.2
pydantic>=2.10.3
python-dotenv>=1.0.1
loguru>=0.7.3
httpx>=0.28.1
```

## 2. Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server.py .

EXPOSE 8000

CMD ["uvicorn", "mcp_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## 3. .env.example

```
OCI_COMPARTMENT_ID=ocid1.compartment.oc1..xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OCI_REGION=us-phoenix-1
LOG_LEVEL=INFO
```

## 4. infra/provider.tf

```hcl
terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 6.0.0"
    }
  }
}

provider "oci" {
  region           = var.region
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
}
```

## 5. infra/variables.tf

```hcl
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
  default = "us-phoenix-1"
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
```

Done. These 5 files complete the infra scaffolding.
