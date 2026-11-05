# Run in an Administrator PowerShell. Limit access to the local subnet.
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$ruleName = 'SmartParking-Rehearsal-LAN'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName `
        -DisplayName 'Smart Parking rehearsal LAN (80,8002,5173)' `
        -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort 80,8002,5173 -RemoteAddress LocalSubnet -Profile Any | Out-Null
}
Write-Host 'LAN firewall rule ready. Use the server LAN IP, not 127.0.0.1, on other PCs.'
