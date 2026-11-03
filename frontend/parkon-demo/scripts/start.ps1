$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.env')) {
    $dbEntropy = New-Object byte[] 32
    $dbGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $dbGenerator.GetBytes($dbEntropy) } finally { $dbGenerator.Dispose() }
    $dbPassword = [Convert]::ToBase64String($dbEntropy)
    [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), "POSTGRES_PASSWORD=$dbPassword`n")
    Write-Host 'Created local .env with a random database password.'
}
docker compose up -d --build --wait
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose startup failed. Run docker compose logs.' }
Write-Host 'Web: http://127.0.0.1:8080'
Write-Host 'API docs: http://127.0.0.1:8000/docs'
