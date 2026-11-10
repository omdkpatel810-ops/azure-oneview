variable "resource_group_name" {
  description = "OneView's resource group, vended by Guardrails from environments/oneview-prod.json."
  type        = string
  default     = "rg-oneview-prod"
}

variable "onboarding_open" {
  description = "True while machines are being connected. Set to false afterwards: the onboarding secret is deleted from Entra ID and Key Vault."
  type        = bool
  default     = true
}

variable "onboarding_round" {
  description = "Bump to rotate the onboarding secret, for example before connecting a new branch."
  type        = number
  default     = 1
}

variable "operator_ip_ranges" {
  description = "Public IP ranges allowed to reach the Key Vault, such as your laptop's address /32. Everyone else is refused."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for r in var.operator_ip_ranges : can(cidrhost(r, 0))])
    error_message = "Use CIDR ranges such as 203.0.113.7/32."
  }
}

# Milestone 2: patching (patching.tf)

variable "patch_time_zone" {
  description = "Time zone for patch windows (Windows time zone name)."
  type        = string
  default     = "Central Standard Time" # Winnipeg
}

variable "patch_start_date" {
  description = "First date the schedules may run, YYYY-MM-DD. Must be in the future when first applied."
  type        = string
  default     = "2026-11-17"

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.patch_start_date))
    error_message = "Use YYYY-MM-DD."
  }
}

variable "patch_rings" {
  description = "Patch window for each ring: weekday, start time (24 h) and length in hours."
  type = map(object({
    day   = string
    start = string
    hours = number
  }))
  default = {
    test   = { day = "Tuesday", start = "22:00", hours = 3 }
    branch = { day = "Thursday", start = "22:00", hours = 3 }
  }

  validation {
    condition     = alltrue([for r in values(var.patch_rings) : contains(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], r.day) && can(regex("^([01][0-9]|2[0-3]):[0-5][0-9]$", r.start)) && r.hours >= 1.5 && r.hours <= 6])
    error_message = "Each ring needs a weekday, a start time like 22:00, and a window of 1.5 to 6 hours."
  }

  validation {
    condition     = contains(keys(var.patch_rings), "test") && contains(keys(var.patch_rings), "branch")
    error_message = "Keep both rings: test, then branch."
  }

  validation {
    condition = alltrue([
      for r in values(var.patch_rings) :
      tonumber(split(":", r.start)[0]) >= 19 || tonumber(split(":", r.start)[0]) + r.hours <= 7
    ])
    error_message = "Patch windows must be after hours: start at 19:00 or later, or end by 07:00."
  }
}

variable "paused_rings" {
  description = "Rings to hold back, for example [\"branch\"] when the test ring found a bad patch. Their windows stay, but no servers are in them."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for r in var.paused_rings : contains(keys(var.patch_rings), r)])
    error_message = "Pause only rings that exist in patch_rings."
  }
}

# Milestone 3: security baselines (baseline.tf)

variable "defender_for_servers" {
  description = "Defender for Servers on the whole subscription: off, P1 or P2. Each costs per server per month; see baseline.tf."
  type        = string
  default     = "off"

  validation {
    condition     = contains(["off", "P1", "P2"], var.defender_for_servers)
    error_message = "Use off, P1 or P2."
  }
}
