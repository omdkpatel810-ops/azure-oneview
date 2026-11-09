variable "region" {
  description = "AWS region for the acquired company's servers. Canada, like everything else."
  type        = string
  default     = "ca-central-1"
}

variable "instance_type" {
  description = "Free-tier eligible size."
  type        = string
  default     = "t3.micro"
}

variable "operator_cidr" {
  description = "Your public IP as a /32: the only address allowed to SSH in."
  type        = string

  validation {
    condition     = can(cidrhost(var.operator_cidr, 0)) && endswith(var.operator_cidr, "/32")
    error_message = "Use your own address as a /32, such as 203.0.113.7/32."
  }
}

variable "ssh_public_key" {
  description = "Your SSH public key, for onboard.py."
  type        = string
}
