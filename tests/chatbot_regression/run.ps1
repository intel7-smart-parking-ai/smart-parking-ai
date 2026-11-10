param(
    [Parameter(Mandatory)][string]$QuestionnaireA,
    [Parameter(Mandatory)][string]$QuestionnaireB,
    [string]$AgentsEnvFile
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$QuestionnaireA = (Resolve-Path -LiteralPath $QuestionnaireA).Path
$QuestionnaireB = (Resolve-Path -LiteralPath $QuestionnaireB).Path
if ($AgentsEnvFile) { $AgentsEnvFile = (Resolve-Path -LiteralPath $AgentsEnvFile).Path }
$fixture = Join-Path $PSScriptRoot 'fixture_b.py'
$output = Join-Path ([IO.Path]::GetTempPath()) ('parkon-regression-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $output | Out-Null
$image = 'parkon-chatbot-regression'
$name = 'parkon-1109-qa'

function Invoke-Docker {
    docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed: $($args[0])" }
}

function Test-Suite([string[]]$Scripts, [bool]$UseFixture) {
    $options = @('run','-d','--rm','--name',$name,'--tmpfs','/data:rw,uid=10001,gid=10001','-p','127.0.0.1:18480:8000',
        '-e','VISION_TOKEN=parkon-qa-vision','-e','ALERTS_INTERNAL_TOKEN=parkon-qa-alert',
        '-e','REPORT_CHAT_ENABLED=1','-e','REPORT_API_BASE=http://127.0.0.1:8000',
        '-e','DATA_SOURCE=api','-e','BACKEND_BASE_URL=http://127.0.0.1:8000','-e','TZ=Asia/Seoul')
    if ($AgentsEnvFile) { $options += @('--env-file',$AgentsEnvFile) }
    if ($UseFixture) { $options += @('--mount',"type=bind,source=$fixture,target=/app/b_fixture.py,readonly") }
    $options += $image
    if ($UseFixture) { $options += @('python','-m','b_fixture') }
    Invoke-Docker @options
    try {
        curl.exe --retry 10 --retry-all-errors --retry-delay 1 -fsS http://127.0.0.1:18480/api/v1/health
        if ($LASTEXITCODE -ne 0) { throw 'QA API did not become ready' }
        foreach ($script in $Scripts) {
            $scriptPath = Join-Path $PSScriptRoot $script
            if ($script -eq 'run_a.py') {
                python -X utf8 -u $scriptPath $QuestionnaireA
            } elseif ($script -eq 'run_b.py') {
                python -X utf8 -u $scriptPath $QuestionnaireB
            } else {
                python -X utf8 -u $scriptPath
            }
            if ($LASTEXITCODE -ne 0) { throw "Regression failed: $script" }
        }
    } finally {
        Invoke-Docker stop $name
    }
}

Invoke-Docker build --file (Join-Path $repo 'backend/Dockerfile.agents') --tag $image $repo
Push-Location $output
try {
    Test-Suite @('run_a.py') $false
    Test-Suite @('run_b.py','run_variants.py') $true
    python -X utf8 -u (Join-Path $PSScriptRoot 'assert_results.py')
    if ($LASTEXITCODE -ne 0) { throw 'Regression contracts failed' }
} finally {
    Pop-Location
    Write-Output "Results: $output"
}
