terraform {
  required_version = ">= 1.11"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.4"
    }
  }

  # State goes in Guardrails' state storage, next to the platform's (see README).
  backend "azurerm" {
    key              = "oneview.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false # purge protection is on; never try
    }
  }
  storage_use_azuread = true
}

provider "azuread" {}
