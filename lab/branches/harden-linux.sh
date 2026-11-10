#!/usr/bin/env bash
# Milestone 3's fix for the weak settings linux.sh planted. Runs only when asked:
#   vagrant provision --provision-with harden
set -euo pipefail

sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/; s/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
chown root:shadow /etc/shadow
chmod 640 /etc/shadow
systemctl restart ssh
echo "Hardened $(hostname): root and password SSH logins off, /etc/shadow readable by root and the shadow group only."
