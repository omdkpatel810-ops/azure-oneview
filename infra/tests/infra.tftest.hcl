# Offline tests: both providers are mocked (tests/mocks), so these need no Azure account.

mock_provider "azurerm" {
  source = "./tests/mocks/azurerm"
}

mock_provider "azuread" {
  source = "./tests/mocks/azuread"
}

variables {
  operator_ip_ranges = ["203.0.113.7/32"]
}

run "onboarding_is_open" {
  command = apply

  assert {
    condition     = azurerm_role_assignment.onboarding.role_definition_name == "Azure Connected Machine Onboarding" && azurerm_role_assignment.onboarding.scope == data.azurerm_resource_group.oneview.id
    error_message = "The onboarding identity may only register machines, and only in OneView's resource group."
  }

  assert {
    condition     = length(azuread_application_password.onboarding) == 1 && length(azurerm_key_vault_secret.onboarding) == 1
    error_message = "While onboarding is open, there's one secret, and it's in Key Vault."
  }

  assert {
    condition     = timecmp(azuread_application_password.onboarding[0].end_date, timeadd(plantimestamp(), "169h")) < 0 && azurerm_key_vault_secret.onboarding[0].expiration_date == azuread_application_password.onboarding[0].end_date
    error_message = "The secret expires within 7 days, in Entra ID and in Key Vault alike."
  }

  assert {
    condition     = output.connect.secret_name == "arc-onboarding-secret" && output.connect.client_id == "00000000-0000-0000-0000-00000000eeee"
    error_message = "The onboarding script gets everything but the secret itself."
  }
}

run "the_vault_is_locked_down" {
  command = apply

  assert {
    condition     = azurerm_key_vault.oneview.rbac_authorization_enabled && azurerm_key_vault.oneview.purge_protection_enabled
    error_message = "Access by Azure roles, and a deleted vault can't be purged."
  }

  assert {
    condition     = azurerm_key_vault.oneview.network_acls[0].default_action == "Deny" && azurerm_key_vault.oneview.network_acls[0].ip_rules == toset(["203.0.113.7/32"])
    error_message = "Only the operator's address can reach the vault."
  }

  assert {
    condition     = azurerm_key_vault.oneview.tags == tomap({ owner = "it-ops-lead@example.com", "cost-center" = "cc-7300", environment = "prod", team = "oneview" })
    error_message = "The vault carries the resource group's tags, so Guardrails' inheritance never causes drift."
  }
}

run "closing_onboarding_deletes_the_secret" {
  command = apply

  variables {
    onboarding_open = false
  }

  assert {
    condition     = length(azuread_application_password.onboarding) == 0 && length(azurerm_key_vault_secret.onboarding) == 0 && output.connect.secret_name == null
    error_message = "Once machines are connected, the secret no longer exists anywhere."
  }

  assert {
    condition     = azurerm_role_assignment.onboarding.principal_id == azuread_service_principal.onboarding.object_id
    error_message = "The identity stays, without a secret, ready for the next round."
  }
}

run "a_new_round_rotates_the_secret" {
  command = plan

  variables {
    onboarding_round = 2
  }

  assert {
    condition     = azuread_application_password.onboarding[0].rotate_when_changed["round"] == "2" && azuread_application_password.onboarding[0].display_name == "onboarding-round-2"
    error_message = "Bumping onboarding_round replaces the secret."
  }
}

run "refuses_a_bad_ip_range" {
  command = plan

  variables {
    operator_ip_ranges = ["my laptop"]
  }

  expect_failures = [var.operator_ip_ranges]
}
