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
