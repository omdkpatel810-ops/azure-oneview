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

run "one_capped_workspace_with_sentinel" {
  command = apply

  assert {
    condition     = azurerm_log_analytics_workspace.oneview.daily_quota_gb == 0.2 && azurerm_log_analytics_workspace.oneview.retention_in_days == 90 && azurerm_log_analytics_workspace.oneview.sku == "PerGB2018"
    error_message = "A daily cap so the bill can't run away, and the 90 days of retention Sentinel includes."
  }

  assert {
    condition     = azurerm_sentinel_log_analytics_workspace_onboarding.oneview.workspace_id == azurerm_log_analytics_workspace.oneview.id
    error_message = "Sentinel is on the workspace."
  }
}

run "collects_sign_ins_security_events_and_errors" {
  command = apply

  assert {
    condition     = toset(flatten([for f in azurerm_monitor_data_collection_rule.servers.data_flow : f.streams])) == toset(["Microsoft-Syslog", "Microsoft-SecurityEvent", "Microsoft-Event", "Microsoft-Perf"])
    error_message = "Syslog, Windows security events, Windows system events and performance, all to the workspace."
  }

  assert {
    condition     = toset(azurerm_monitor_data_collection_rule.servers.data_sources[0].syslog[0].facility_names) == toset(["auth", "authpriv"])
    error_message = "Only Linux sign-in logs, to keep the volume down."
  }

  assert {
    condition = alltrue([for id in ["4624", "4625", "4720", "4732"] :
      strcontains(one([for w in azurerm_monitor_data_collection_rule.servers.data_sources[0].windows_event_log : w if w.name == "security"]).x_path_queries[0], "EventID=${id}")
    ])
    error_message = "The Windows security events the alert rules need."
  }
}

run "four_alert_rules_from_detections" {
  command = apply

  assert {
    condition     = toset(keys(azurerm_sentinel_alert_rule_scheduled.detection)) == toset(["ssh-brute-force", "ssh-root-login", "windows-failed-logons", "windows-new-local-admin"])
    error_message = "One rule per file in detections/."
  }

  assert {
    condition = alltrue([for r in values(azurerm_sentinel_alert_rule_scheduled.detection) :
      r.query_frequency == "PT10M" && r.query_period == "PT10M" && r.trigger_threshold == 0 && r.incident[0].create_incident_enabled && startswith(r.display_name, "OneView: ")
    ])
    error_message = "Every 10 minutes, any row is an alert, and alerts become incidents."
  }

  assert {
    condition     = strcontains(azurerm_sentinel_alert_rule_scheduled.detection["windows-new-local-admin"].query, "S-1-5-32-544") && azurerm_sentinel_alert_rule_scheduled.detection["ssh-root-login"].severity == "High"
    error_message = "The queries come from detections/, with their severity."
  }

  assert {
    condition     = toset([for m in azurerm_sentinel_alert_rule_scheduled.detection["ssh-root-login"].entity_mapping : "${m.entity_type}=${m.field_mapping[0].identifier}:${m.field_mapping[0].column_name}"]) == toset(["Host=HostName:Host", "IP=Address:SourceIP", "Account=Name:Account"])
    error_message = "Incidents name the server, the address and the account."
  }
}

run "no_agents_until_the_servers_are_connected" {
  command = plan

  assert {
    condition     = length(azurerm_arc_machine_extension.monitor_agent) == 0 && length(azurerm_monitor_data_collection_rule_association.server) == 0
    error_message = "The servers don't exist in Azure until onboard.py has run."
  }
}

run "an_agent_on_every_server_once_connected" {
  command = apply

  variables {
    machines_connected = true
  }

  assert {
    condition     = length(azurerm_arc_machine_extension.monitor_agent) == 8 && length(azurerm_monitor_data_collection_rule_association.server) == 8
    error_message = "All eight servers from lab/inventory.json."
  }

  assert {
    condition     = azurerm_arc_machine_extension.monitor_agent["branch-05-winkler"].type == "AzureMonitorWindowsAgent" && azurerm_arc_machine_extension.monitor_agent["acquired-01"].type == "AzureMonitorLinuxAgent"
    error_message = "The right agent for each operating system."
  }

  assert {
    condition     = alltrue([for e in values(azurerm_arc_machine_extension.monitor_agent) : e.publisher == "Microsoft.Azure.Monitor" && e.automatic_upgrade_enabled])
    error_message = "Microsoft's agent, kept up to date."
  }

  assert {
    condition     = alltrue([for a in values(azurerm_monitor_data_collection_rule_association.server) : a.data_collection_rule_id == azurerm_monitor_data_collection_rule.servers.id])
    error_message = "Every server uses the one rule."
  }

  assert {
    condition     = length(output.logs.servers) == 8 && length(output.logs.alert_rules) == 4
    error_message = "The outputs list the servers and rules."
  }
}

run "refuses_an_uncapped_workspace" {
  command = plan

  variables {
    log_daily_cap_gb = 50
  }

  expect_failures = [var.log_daily_cap_gb]
}
