output "public_ips" {
  description = "Public IP of each server, by its inventory host name (aws:<name>). onboard.py reads this."
  value       = { for k, i in aws_instance.acquired : k => i.public_ip }
}
