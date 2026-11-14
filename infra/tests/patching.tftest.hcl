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

run "two_rings_test_first" {
  command = apply

  assert {
    condition     = azurerm_maintenance_configuration.ring["test"].window[0].recur_every == "Week Tuesday" && azurerm_maintenance_configuration.ring["branch"].window[0].recur_every == "Week Thursday"
    error_message = "The test ring patches on Tuesday; branches two days later."
  }

  assert {
    condition = alltrue([for mc in values(azurerm_maintenance_configuration.ring) :
      mc.window[0].start_date_time == "2026-11-17 22:00" && mc.window[0].duration == "03:00" && mc.window[0].time_zone == "Central Standard Time"
    ])
    error_message = "Three-hour windows from 10 p.m. Winnipeg time."
  }

  assert {
    condition = alltrue([for mc in values(azurerm_maintenance_configuration.ring) :
      mc.scope == "InGuestPatch" && mc.install_patches[0].reboot == "IfRequired" &&
      mc.install_patches[0].linux[0].classifications_to_include == tolist(["Critical", "Security"]) &&
      mc.install_patches[0].windows[0].classifications_to_include == tolist(["Critical", "Security"])
    ])
    error_message = "Critical and security updates on both systems, rebooting only when needed."
  }

  assert {
    condition = alltrue([for ring, s in azurerm_maintenance_assignment_dynamic_scope.ring :
      s.filter[0].tags[0].tag == "ring" && s.filter[0].tags[0].values == tolist([ring]) &&
      s.filter[0].resource_types == tolist(["Microsoft.HybridCompute/machines"]) && s.filter[0].resource_groups == tolist(["rg-oneview-prod"])
    ])
    error_message = "Each window picks up Arc servers by their ring tag, in OneView's resource group only."
  }

  assert {
    condition     = output.patch_windows["test"] == "Week Tuesday at 22:00 Central Standard Time, 03:00 h"
    error_message = "The windows are readable in the outputs."
  }
}

run "daily_assessment_on_both_systems" {
  command = apply

  assert {
    condition = alltrue([for os, a in azurerm_resource_group_policy_assignment.assessment :
      endswith(a.policy_definition_id, "/bfea026e-043f-4ff4-9d1b-bf301ca7ff46") && jsondecode(a.parameters).osType.value == os
    ])
    error_message = "Microsoft's periodic-assessment policy for Arc servers, once per operating system."
  }

  assert {
    condition     = alltrue([for r in values(azurerm_role_assignment.assessment) : r.role_definition_name == "Azure Connected Machine Resource Administrator" && r.scope == data.azurerm_resource_group.oneview.id])
    error_message = "The policies may change Arc servers' settings in OneView's resource group, and nothing else."
  }
}

run "half_hours_are_allowed" {
  command = plan

  variables {
    patch_rings = {
      test   = { day = "Tuesday", start = "21:00", hours = 1.5 }
      branch = { day = "Thursday", start = "02:00", hours = 4 }
    }
  }

  assert {
    condition     = azurerm_maintenance_configuration.ring["test"].window[0].duration == "01:30"
    error_message = "1.5 hours is written 01:30."
  }
}

run "refuses_a_window_in_business_hours" {
  command = plan

  variables {
    patch_rings = {
      test   = { day = "Tuesday", start = "12:00", hours = 2 }
      branch = { day = "Thursday", start = "22:00", hours = 3 }
    }
  }

  expect_failures = [var.patch_rings]
}

run "refuses_dropping_the_test_ring" {
  command = plan

  variables {
    patch_rings = {
      branch = { day = "Thursday", start = "22:00", hours = 3 }
    }
  }

  expect_failures = [var.patch_rings]
}

run "refuses_a_made_up_day" {
  command = plan

  variables {
    patch_rings = {
      test   = { day = "Caturday", start = "22:00", hours = 3 }
      branch = { day = "Thursday", start = "22:00", hours = 3 }
    }
  }

  expect_failures = [var.patch_rings]
}

run "pausing_the_branches_keeps_their_window_but_empties_it" {
  command = plan

  variables {
    paused_rings = ["branch"]
  }

  assert {
    condition     = keys(azurerm_maintenance_assignment_dynamic_scope.ring) == ["test"] && length(azurerm_maintenance_configuration.ring) == 2
    error_message = "A paused ring keeps its schedule, with no servers in it."
  }
}

run "refuses_pausing_a_ring_that_doesnt_exist" {
  command = plan

  variables {
    paused_rings = ["branches"]
  }

  expect_failures = [var.paused_rings]
}
