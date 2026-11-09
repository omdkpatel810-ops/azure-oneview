terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Local state: two throwaway VMs on your own AWS account, destroyed when the project ends.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "oneview", role = "acquired-company-server" }
  }
}
