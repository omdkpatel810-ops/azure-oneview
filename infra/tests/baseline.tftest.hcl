# Offline tests: both providers are mocked, so these need no Azure account.

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-00000000aaaa"
      subscription_id = "00000000-0000-0000-0000-00000000bbbb"
      object_id       = "00000000-0000-0000-0000-00000000cccc"
    }
  }

  mock_data "azurerm_resource_group" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod"
      location = "canadacentral"
      tags     = { owner = "it-ops-lead@example.com", "cost-center" = "cc-7300", environment = "prod", team = "oneview", request = "oneview-prod.json" }
    }
  }

  mock_resource "azurerm_maintenance_configuration" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.Maintenance/maintenanceConfigurations/mc" }
  }

  mock_resource "azurerm_resource_group_policy_assignment" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.Authorization/policyAssignments/pa" }
  }

  mock_resource "azurerm_key_vault" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.KeyVault/vaults/kv-oneview" }
  }
}

mock_provider "azuread" {
  mock_resource "azuread_application" {
    defaults = {
      id        = "/applications/00000000-0000-0000-0000-00000000dddd"
      client_id = "00000000-0000-0000-0000-00000000eeee"
    }
  }

  mock_resource "azuread_service_principal" {
    defaults = { object_id = "00000000-0000-0000-0000-00000000ffff" }
  }
}

variables {
  operator_ip_ranges = ["203.0.113.7/32"]
}

run "both_baselines_include_arc_servers" {
  command = plan

  assert {
    condition     = endswith(azurerm_resource_group_policy_assignment.baseline["Linux"].policy_definition_id, "/fc9b3da7-8347-4380-8e70-0a0361d8dedd") && endswith(azurerm_resource_group_policy_assignment.baseline["Windows"].policy_definition_id, "/72650e9f-97bc-4b2a-ab5f-9781a9fcecbc")
    error_message = "Microsoft's Linux and Windows compute security baselines."
  }

  assert {
    condition     = alltrue([for a in values(azurerm_resource_group_policy_assignment.baseline) : jsondecode(a.parameters).IncludeArcMachines.value == "true"])
    error_message = "Without IncludeArcMachines the baselines skip every Arc server."
  }

  assert {
    condition     = alltrue([for a in values(azurerm_resource_group_policy_assignment.baseline) : a.resource_group_id == data.azurerm_resource_group.oneview.id])
    error_message = "Assigned to OneView's resource group only."
  }
}

run "defender_is_off_unless_asked_for" {
  command = plan

  assert {
    condition     = length(azurerm_security_center_subscription_pricing.servers) == 0
    error_message = "Defender for Servers costs money for the whole subscription, so it's opt-in."
  }
}

run "defender_plan_1" {
  command = plan

  variables {
    defender_for_servers = "P1"
  }

  assert {
    condition     = azurerm_security_center_subscription_pricing.servers[0].tier == "Standard" && azurerm_security_center_subscription_pricing.servers[0].subplan == "P1" && azurerm_security_center_subscription_pricing.servers[0].resource_type == "VirtualMachines"
    error_message = "Plan 1 for servers."
  }
}

run "refuses_an_unknown_defender_plan" {
  command = plan

  variables {
    defender_for_servers = "P3"
  }

  expect_failures = [var.defender_for_servers]
}
