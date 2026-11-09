# A neglected Windows branch server: Windows Update off, SSH on for onboard.py, and settings a
# security baseline flags.
$ErrorActionPreference = 'Stop'

# SSH, so onboard.py can reach it the same way as the Linux servers.
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
New-NetFirewallRule -Name 'OpenSSH-Server' -DisplayName 'OpenSSH Server' -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction SilentlyContinue | Out-Null
if (Test-Path C:\tmp\operator.pub) {
  Get-Content C:\tmp\operator.pub | Add-Content -Path "$env:ProgramData\ssh\administrators_authorized_keys"
  icacls "$env:ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
}

# Nobody patches it: Windows Update set to never install.
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate -Value 1

# Weak settings for the security baseline (milestone 3) to find.
net accounts /minpwlen:0 /maxpwage:unlimited | Out-Null
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name LimitBlankPasswordUse -Value 0
