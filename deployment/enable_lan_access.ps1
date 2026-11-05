# Run in an Administrator PowerShell. Limit access to the local subnet.
#Requires -RunAsAdministrator
param(
    [switch]$DockerLocalhostRelay,
    [ValidateRange(1024,65535)][int]$UpstreamPort = 18080
)
$ErrorActionPreference = 'Stop'
$ruleName = 'SmartParking-Rehearsal-LAN'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName `
        -DisplayName 'Smart Parking rehearsal LAN (80,8002,5173)' `
        -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort 80,8002,5173 -RemoteAddress LocalSubnet -Profile Any | Out-Null
}
if ($DockerLocalhostRelay) {
    # Optional Windows workaround: private .env must use
    # WEB_BIND_ADDRESS=127.0.0.1 and WEB_PORT=18080 (or UpstreamPort).
    # External clients still use normal port80. Existing nginx handles auth.
    Start-Service iphlpsvc
    & netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=80 connectaddress=127.0.0.1 connectport=$UpstreamPort protocol=tcp
    if ($LASTEXITCODE -ne 0) { throw 'Windows port80 forwarding failed.' }
    Restart-Service iphlpsvc -Force
}
Write-Host 'LAN firewall rule ready. Use the server LAN IP, not 127.0.0.1, on other PCs.'
