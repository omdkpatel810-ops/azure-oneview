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
