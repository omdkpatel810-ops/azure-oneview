#!/usr/bin/env bash
# A neglected Linux branch server: updates switched off, and SSH settings a CIS baseline flags.
set -euo pipefail

# Let your key in, for onboard.py.
if [[ -f /tmp/operator.pub ]]; then
  install -d -m 700 -o vagrant -g vagrant /home/vagrant/.ssh
  cat /tmp/operator.pub >> /home/vagrant/.ssh/authorized_keys
  chown vagrant:vagrant /home/vagrant/.ssh/authorized_keys
  chmod 600 /home/vagrant/.ssh/authorized_keys
fi

# Nobody patches it: no automatic security updates.
systemctl disable --now unattended-upgrades.service apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true

# Weak settings for the CIS baseline (milestone 3) to find.
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin yes/; s/^#\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config
chmod 644 /etc/shadow
systemctl restart ssh
