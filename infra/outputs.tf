output "connect" {
  description = "What onboarding/onboard.py needs to connect machines. The secret itself stays in Key Vault."
  value = {
    tenant_id       = data.azurerm_client_config.current.tenant_id
    subscription_id = data.azurerm_client_config.current.subscription_id
    resource_group  = data.azurerm_resource_group.oneview.name
    location        = data.azurerm_resource_group.oneview.location
    client_id       = azuread_application.onboarding.client_id
    key_vault       = azurerm_key_vault.oneview.name
    secret_name     = var.onboarding_open ? azurerm_key_vault_secret.onboarding[0].name : null
  }
}

output "patch_windows" {
  description = "Each ring's patch window."
  value       = { for ring, mc in azurerm_maintenance_configuration.ring : ring => "${mc.window[0].recur_every} at ${var.patch_rings[ring].start} ${var.patch_time_zone}, ${mc.window[0].duration} h" }
}

output "baseline_policies" {
  description = "The baseline policy assignments, for checking compliance in the portal."
  value       = { for os, a in azurerm_resource_group_policy_assignment.baseline : os => a.id }
}

output "logs" {
  description = "Where the servers' logs go, and the alert rules watching them."
  value = {
    workspace   = azurerm_log_analytics_workspace.oneview.name
    servers     = sort(keys(azurerm_monitor_data_collection_rule_association.server))
    alert_rules = sort([for r in azurerm_sentinel_alert_rule_scheduled.detection : r.display_name])
  }
}
