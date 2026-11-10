# Milestone 3's fix for the weak settings windows.ps1 planted. Runs only when asked:
#   vagrant provision --provision-with harden
$ErrorActionPreference = 'Stop'

net accounts /minpwlen:14 /maxpwage:70 | Out-Null
Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name LimitBlankPasswordUse -Value 1
Write-Output "Hardened ${env:COMPUTERNAME}: passwords at least 14 characters and changed within 70 days, blank passwords refused."
