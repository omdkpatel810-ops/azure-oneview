# Shared stand-ins for Azure, used by every test file.

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

mock_data "azurerm_arc_machine" {
  defaults = {
    id       = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.HybridCompute/machines/server"
    location = "canadacentral"
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

mock_resource "azurerm_log_analytics_workspace" {
  defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.OperationalInsights/workspaces/log-oneview" }
}

mock_resource "azurerm_monitor_data_collection_rule" {
  defaults = { id = "/subscriptions/00000000-0000-0000-0000-00000000bbbb/resourceGroups/rg-oneview-prod/providers/Microsoft.Insights/dataCollectionRules/dcr-oneview-servers" }
}
