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
