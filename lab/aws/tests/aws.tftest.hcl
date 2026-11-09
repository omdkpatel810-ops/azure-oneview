# Offline tests: the aws provider is mocked, so these need no AWS account.

mock_provider "aws" {
  mock_data "aws_ami" {
    defaults = { id = "ami-0123456789abcdef0" }
  }

  mock_resource "aws_instance" {
    defaults = { public_ip = "198.51.100.10" }
  }
}

variables {
  operator_cidr  = "203.0.113.7/32"
  ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMrXtestkeyonly operator"
}

run "two_servers_from_the_inventory" {
  command = apply

  assert {
    condition     = toset(keys(aws_instance.acquired)) == toset(["acquired-01", "acquired-02"]) && toset(keys(output.public_ips)) == toset(["acquired-01", "acquired-02"])
    error_message = "One server per aws: entry in lab/inventory.json, named as onboard.py expects."
  }

  assert {
    condition     = aws_instance.acquired["acquired-02"].tags["ring"] == "test" && aws_instance.acquired["acquired-01"].tags["ring"] == "branch"
    error_message = "Each server carries its patch ring."
  }
}

run "locked_down_where_it_matters" {
  command = apply

  assert {
    condition     = one(aws_security_group.acquired.ingress).cidr_blocks == tolist(["203.0.113.7/32"]) && one(aws_security_group.acquired.ingress).from_port == 22
    error_message = "Only the operator can reach the servers, and only over SSH."
  }

  assert {
    condition     = alltrue([for i in values(aws_instance.acquired) : i.metadata_options[0].http_tokens == "required" && i.root_block_device[0].encrypted])
    error_message = "IMDSv2 only, and encrypted disks."
  }

  assert {
    condition     = alltrue([for i in values(aws_instance.acquired) : i.instance_type == "t3.micro"])
    error_message = "Free-tier sizes."
  }
}

run "refuses_ssh_from_anywhere" {
  command = plan

  variables {
    operator_cidr = "0.0.0.0/0"
  }

  expect_failures = [var.operator_cidr]
}
