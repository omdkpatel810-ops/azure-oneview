# Milestone 3: security baselines.
#
# Microsoft's Azure compute security baseline (built from the CIS benchmarks) checked on every Arc
# server through Azure Policy machine configuration. The Connected Machine agent runs the checks
# itself, every 15 minutes or so, and reports each rule as passing or failing, with the reason; no
# extension or extra identity is needed on Arc servers. The policies only audit: they report what's
# wrong and change nothing, so fixing is deliberate (lab/branches/harden-*).

locals {
  baselines = {
    Linux   = "fc9b3da7-8347-4380-8e70-0a0361d8dedd" # Linux machines should meet requirements for the Azure compute security baseline
    Windows = "72650e9f-97bc-4b2a-ab5f-9781a9fcecbc" # Windows machines should meet requirements of the Azure compute security baseline
  }
}

resource "azurerm_resource_group_policy_assignment" "baseline" {
  for_each = local.baselines

  name                 = "oneview-baseline-${lower(each.key)}"
  display_name         = "OneView: ${each.key} servers meet the Azure compute security baseline"
  resource_group_id    = data.azurerm_resource_group.oneview.id
  policy_definition_id = "/providers/Microsoft.Authorization/policyDefinitions/${each.value}"
  # Without this the policies skip Arc servers and check only Azure VMs.
  parameters = jsonencode({ IncludeArcMachines = { value = "true" } })

  non_compliance_message {
    content = "This server fails part of the Azure compute security baseline. Run python -m oneview.baseline snapshot to see which rules."
  }
}

# Defender for Servers, optional and off by default. It applies to the whole subscription, not just
# OneView, and costs about $5 (Plan 1) or $15 (Plan 2) per server per month. Plan 2 includes Update
# Manager for Arc servers, so it costs about $5 more than Plan 1 plus Update Manager.
resource "azurerm_security_center_subscription_pricing" "servers" {
  count = var.defender_for_servers == "off" ? 0 : 1

  tier          = "Standard"
  resource_type = "VirtualMachines"
  subplan       = var.defender_for_servers
}
