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
