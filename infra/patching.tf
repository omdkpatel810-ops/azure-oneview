# Milestone 2: patching with a test ring.
#
# Two weekly windows in Azure Update Manager, each picking up servers by their `ring` tag (set by
# onboard.py), so a new branch server is patched on the right night without touching this file:
#   - test ring: Tuesday nights, so problems show up on three servers first;
#   - branches:  Thursday nights, after two days of the test ring running the same patches,
#                and outside branch business hours.
# Both install critical and security updates and reboot only when an update needs it. Arc also checks
# every server for missing updates daily, so the compliance report is never more than a day old.
# The windows themselves are set by patch_rings in variables.tf.

locals {
  classifications = ["Critical", "Security"]
}

resource "azurerm_maintenance_configuration" "ring" {
  for_each = var.patch_rings

  name                     = "mc-oneview-${each.key}"
  resource_group_name      = data.azurerm_resource_group.oneview.name
  location                 = data.azurerm_resource_group.oneview.location
  scope                    = "InGuestPatch"
  in_guest_user_patch_mode = "User"
  visibility               = "Custom"
  tags                     = merge(local.tags, { ring = each.key })

  window {
    start_date_time = "${var.patch_start_date} ${each.value.start}"
    duration        = format("%02d:%02d", floor(each.value.hours), (each.value.hours - floor(each.value.hours)) * 60)
    time_zone       = var.patch_time_zone
    recur_every     = "Week ${each.value.day}"
  }

  install_patches {
    reboot = "IfRequired"

    linux {
      classifications_to_include = local.classifications
    }

    windows {
      classifications_to_include = local.classifications
    }
  }
}

# Which servers each window patches: Arc servers in OneView's resource group tagged with that ring.
# The test ring runs first, but nothing stops the branches' window by itself: if the test ring finds
# a bad patch, pause the branches (paused_rings = ["branch"]) and apply before their window opens.
resource "azurerm_maintenance_assignment_dynamic_scope" "ring" {
  for_each = { for ring, w in var.patch_rings : ring => w if !contains(var.paused_rings, ring) }

  name                         = "oneview-${each.key}-servers"
  maintenance_configuration_id = azurerm_maintenance_configuration.ring[each.key].id

  filter {
    resource_types  = ["Microsoft.HybridCompute/machines"]
    resource_groups = [data.azurerm_resource_group.oneview.name]
    locations       = [data.azurerm_resource_group.oneview.location]
    os_types        = ["Linux", "Windows"]
    tag_filter      = "Any"

    tags {
      tag    = "ring"
      values = [each.key]
    }
  }
}

# Daily checks for missing updates on every Arc server, through Microsoft's built-in policy
# (one assignment per operating system, as the policy requires).
resource "azurerm_resource_group_policy_assignment" "assessment" {
  for_each = toset(["Linux", "Windows"])

  name                 = "oneview-assess-${lower(each.key)}"
  display_name         = "OneView: check ${each.key} Arc servers for missing updates daily"
  resource_group_id    = data.azurerm_resource_group.oneview.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/bfea026e-043f-4ff4-9d1b-bf301ca7ff46"
  location             = data.azurerm_resource_group.oneview.location
  parameters           = jsonencode({ osType = { value = each.key } })

  identity {
    type = "SystemAssigned"
  }
}

# The policy switches each server's assessment mode, which needs this role on the resource group.
resource "azurerm_role_assignment" "assessment" {
  for_each = azurerm_resource_group_policy_assignment.assessment

  scope                = data.azurerm_resource_group.oneview.id
  role_definition_name = "Azure Connected Machine Resource Administrator"
  principal_id         = each.value.identity[0].principal_id
  principal_type       = "ServicePrincipal"
  description          = "Policy ${each.value.name}: set periodic patch assessment on Arc servers."
}
