# Milestone 1: everything needed to bring outside servers under Azure management with Azure Arc.
#
# Machines connect with a service principal that can do exactly one thing: register machines in
# OneView's resource group. Its secret lives only in Key Vault, expires within 7 days, and is deleted
# outright when onboarding closes (onboarding_open = false).

data "azurerm_client_config" "current" {}

data "azurerm_resource_group" "oneview" {
  name = var.resource_group_name
}

locals {
  suffix = substr(sha1("${data.azurerm_client_config.current.subscription_id}-oneview"), 0, 8)
  # Same tags as the resource group, so Guardrails' tag inheritance never shows up as drift.
  tags = { for k in ["owner", "cost-center", "environment", "team"] : k => data.azurerm_resource_group.oneview.tags[k] if contains(keys(data.azurerm_resource_group.oneview.tags), k) }
}

# --- Key Vault --------------------------------------------------------------------------------------

resource "azurerm_key_vault" "oneview" {
  #checkov:skip=CKV2_AZURE_32:A private endpoint would need the operator inside the network; access is limited by IP and RBAC instead.
  #checkov:skip=CKV_AZURE_189:The operator's laptop reaches the vault over the internet; network_acls refuse every other address.
  name                          = "kv-oneview-${local.suffix}"
  resource_group_name           = data.azurerm_resource_group.oneview.name
  location                      = data.azurerm_resource_group.oneview.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true # roles, not access policies
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = true # reachable only from operator_ip_ranges, below
  tags                          = local.tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = var.operator_ip_ranges
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Whoever applies this (the subscription owner) manages the secret; nobody else reads it.
resource "azurerm_role_assignment" "operator_secrets" {
  scope                = azurerm_key_vault.oneview.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
  description          = "OneView operator: write and read the Arc onboarding secret."
}

# --- The onboarding identity ------------------------------------------------------------------------

resource "azuread_application" "onboarding" {
  display_name     = "oneview-arc-onboarding"
  description      = "Connects branch and acquired servers to Azure Arc in ${var.resource_group_name}. Nothing else."
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azurerm_client_config.current.object_id]
}

resource "azuread_service_principal" "onboarding" {
  client_id = azuread_application.onboarding.client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

# "Azure Connected Machine Onboarding" can create Arc machines and nothing else: it can't read,
# change or delete them afterwards, and only in this resource group.
resource "azurerm_role_assignment" "onboarding" {
  scope                = data.azurerm_resource_group.oneview.id
  role_definition_name = "Azure Connected Machine Onboarding"
  principal_id         = azuread_service_principal.onboarding.object_id
  principal_type       = "ServicePrincipal"
  description          = "Arc onboarding into ${var.resource_group_name} only."
}

resource "azuread_application_password" "onboarding" {
  count = var.onboarding_open ? 1 : 0

  application_id      = azuread_application.onboarding.id
  display_name        = "onboarding-round-${var.onboarding_round}"
  end_date            = timeadd(plantimestamp(), "168h")
  rotate_when_changed = { round = tostring(var.onboarding_round) }

  lifecycle {
    ignore_changes = [end_date]
  }
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy # deleting it when onboarding closes is the point
resource "azurerm_key_vault_secret" "onboarding" {
  count = var.onboarding_open ? 1 : 0

  name            = "arc-onboarding-secret"
  key_vault_id    = azurerm_key_vault.oneview.id
  value           = azuread_application_password.onboarding[0].value
  content_type    = "Entra ID client secret for ${azuread_application.onboarding.display_name}"
  expiration_date = azuread_application_password.onboarding[0].end_date
  depends_on      = [azurerm_role_assignment.operator_secrets]
}
