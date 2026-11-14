# Milestone 4: every server's logs in one place, watched by Microsoft Sentinel.
#
# A Log Analytics workspace with Sentinel on it, and one data collection rule for all eight servers:
#   - Linux: sign-in logs (syslog auth and authpriv);
#   - Windows: sign-in and group-change security events, and System log warnings and errors;
#   - both: processor, memory and disk use every 5 minutes.
# The Azure Monitor Agent goes on each server as an Arc extension and is linked to the rule. Four
# alert rules (detections/) raise incidents in Sentinel.
#
# The agent and its link need the servers to exist in Azure, so they wait for machines_connected = true,
# set after onboard.py has connected them.

locals {
  inventory  = jsondecode(file("${path.module}/../lab/inventory.json")).machines
  machines   = { for m in local.inventory : m.name => m.os }
  detections = jsondecode(file("${path.module}/../detections/rules.json"))
  agents = {
    linux   = "AzureMonitorLinuxAgent"
    windows = "AzureMonitorWindowsAgent"
  }
}

resource "azurerm_log_analytics_workspace" "oneview" {
  name                = "log-oneview-${local.suffix}"
  resource_group_name = data.azurerm_resource_group.oneview.name
  location            = data.azurerm_resource_group.oneview.location
  sku                 = "PerGB2018"
  retention_in_days   = 90 # included with Sentinel
  daily_quota_gb      = var.log_daily_cap_gb
  tags                = local.tags

  # Keep the logs if someone deletes this stack by mistake; they are the audit trail.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_sentinel_log_analytics_workspace_onboarding" "oneview" {
  workspace_id = azurerm_log_analytics_workspace.oneview.id
}

resource "azurerm_monitor_data_collection_rule" "servers" {
  name                = "dcr-oneview-servers"
  resource_group_name = data.azurerm_resource_group.oneview.name
  location            = data.azurerm_resource_group.oneview.location
  description         = "Sign-in logs, security events, system errors and basic performance from OneView's servers."
  tags                = local.tags

  destinations {
    log_analytics {
      name                  = "oneview"
      workspace_resource_id = azurerm_log_analytics_workspace.oneview.id
    }
  }

  data_flow {
    streams      = ["Microsoft-Syslog"]
    destinations = ["oneview"]
  }

  data_flow {
    streams      = ["Microsoft-SecurityEvent"] # needs Sentinel on the workspace
    destinations = ["oneview"]
  }

  data_flow {
    streams      = ["Microsoft-Event"]
    destinations = ["oneview"]
  }

  data_flow {
    streams      = ["Microsoft-Perf"]
    destinations = ["oneview"]
  }

  data_sources {
    syslog {
      name           = "sign-ins"
      facility_names = ["auth", "authpriv"]
      log_levels     = ["Info", "Notice", "Warning", "Error", "Critical", "Alert", "Emergency"]
      streams        = ["Microsoft-Syslog"]
    }

    windows_event_log {
      name    = "security"
      streams = ["Microsoft-SecurityEvent"]
      # Logins (4624), failed logins (4625), new accounts (4720), and local group additions (4732).
      x_path_queries = ["Security!*[System[(EventID=4624 or EventID=4625 or EventID=4720 or EventID=4732)]]"]
    }

    windows_event_log {
      name           = "system-errors"
      streams        = ["Microsoft-Event"]
      x_path_queries = ["System!*[System[(Level=1 or Level=2 or Level=3)]]"]
    }

    performance_counter {
      name                          = "basics"
      streams                       = ["Microsoft-Perf"]
      sampling_frequency_in_seconds = 300
      counter_specifiers = [
        "\\Processor Information(_Total)\\% Processor Time",
        "\\Memory\\% Committed Bytes In Use",
        "\\LogicalDisk(_Total)\\% Free Space",
        "\\Processor(*)\\% Processor Time",
        "\\Memory(*)\\% Used Memory",
        "\\Logical Disk(*)\\% Free Space",
      ]
    }
  }

  depends_on = [azurerm_sentinel_log_analytics_workspace_onboarding.oneview]
}

# The servers, once onboard.py has connected them.
data "azurerm_arc_machine" "server" {
  for_each = var.machines_connected ? local.machines : {}

  name                = each.key
  resource_group_name = data.azurerm_resource_group.oneview.name
}

resource "azurerm_arc_machine_extension" "monitor_agent" {
  for_each = data.azurerm_arc_machine.server

  name                      = local.agents[local.machines[each.key]]
  arc_machine_id            = each.value.id
  location                  = each.value.location
  publisher                 = "Microsoft.Azure.Monitor"
  type                      = local.agents[local.machines[each.key]]
  automatic_upgrade_enabled = true
  tags                      = local.tags
}

resource "azurerm_monitor_data_collection_rule_association" "server" {
  for_each = data.azurerm_arc_machine.server

  name                    = "oneview-${each.key}"
  target_resource_id      = each.value.id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.servers.id
  description             = "Sends ${each.key}'s logs to OneView's workspace."
}

# Alert rules: each query runs every 10 minutes over the last 10 minutes; any row it returns is an
# alert, grouped into one incident per server for a day.
resource "azurerm_sentinel_alert_rule_scheduled" "detection" {
  for_each = local.detections

  name                       = "oneview-${each.key}"
  log_analytics_workspace_id = azurerm_sentinel_log_analytics_workspace_onboarding.oneview.workspace_id
  display_name               = "OneView: ${each.value.display_name}"
  description                = each.value.description
  severity                   = each.value.severity
  tactics                    = each.value.tactics
  techniques                 = each.value.techniques
  query                      = file("${path.module}/../detections/${each.key}.kql")
  query_frequency            = "PT10M"
  query_period               = "PT10M"
  trigger_operator           = "GreaterThan"
  trigger_threshold          = 0

  event_grouping {
    aggregation_method = "AlertPerResult"
  }

  dynamic "entity_mapping" {
    for_each = each.value.entities
    content {
      entity_type = entity_mapping.key
      field_mapping {
        identifier  = { Host = "HostName", IP = "Address", Account = "Name" }[entity_mapping.key]
        column_name = entity_mapping.value
      }
    }
  }

  incident {
    create_incident_enabled = true
    grouping {
      enabled                 = true
      lookback_duration       = "P1D"
      entity_matching_method  = "Selected"
      by_entities             = ["Host"]
      reopen_closed_incidents = false
    }
  }
}
