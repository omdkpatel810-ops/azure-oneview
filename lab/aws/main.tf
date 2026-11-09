# The acquired company's two servers, in another cloud: Ubuntu on AWS's free tier, built from a
# 2024 image so they arrive with a year of missing patches, like servers nobody has looked after.

locals {
  inventory = jsondecode(file("${path.module}/../inventory.json"))
  machines  = { for m in local.inventory.machines : trimprefix(m.host, "aws:") => m if m.cloud == "aws" }
}

data "aws_ami" "ubuntu_2024" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-2024*"]
  }
}

resource "aws_key_pair" "operator" {
  key_name   = "oneview-operator"
  public_key = var.ssh_public_key
}

resource "aws_security_group" "acquired" {
  #checkov:skip=CKV_AWS_382:The servers reach Azure Arc's endpoints and Ubuntu's mirrors, which have no fixed addresses.
  name        = "oneview-acquired"
  description = "SSH from the operator only"

  ingress {
    description = "SSH from the operator"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.operator_cidr]
  }

  egress {
    description = "HTTPS out, to Azure Arc and package mirrors"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "HTTP out, for Ubuntu package mirrors"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "acquired" {
  #checkov:skip=CKV_AWS_88:The operator connects over the internet, as with any acquired company's servers; SSH is open to one address only.
  #checkov:skip=CKV_AWS_126:Detailed monitoring costs extra; Azure Monitor collects metrics once the servers are connected to Arc.
  #checkov:skip=CKV2_AWS_41:The servers need no AWS permissions.
  for_each = local.machines

  ami                         = data.aws_ami.ubuntu_2024.id
  instance_type               = var.instance_type
  key_name                    = aws_key_pair.operator.key_name
  vpc_security_group_ids      = [aws_security_group.acquired.id]
  associate_public_ip_address = true
  ebs_optimized               = true
  monitoring                  = false

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    encrypted   = true
    volume_type = "gp3"
    volume_size = 8
  }

  # No automatic updates: the missing patches are what milestone 2 fixes.
  user_data = <<-EOT
    #!/bin/bash
    systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer || true
  EOT

  tags = { Name = each.value.name, ring = each.value.ring }
}
