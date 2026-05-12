# Tests Make.com API credentials from .env without ever printing the token.
# Run from the project root:  .\scripts\test_api.ps1

$ErrorActionPreference = 'Stop'

$envPath = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envPath)) {
    Write-Host "KO: .env not found at $envPath" -ForegroundColor Red
    exit 1
}

# Parse .env into a hashtable. Skips comments and blank lines.
$envVars = @{}
foreach ($line in Get-Content $envPath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $parts = $trimmed -split '=', 2
    if ($parts.Count -eq 2) {
        $envVars[$parts[0].Trim()] = $parts[1].Trim()
    }
}

$required = 'MAKE_API_TOKEN', 'MAKE_API_BASE_URL', 'MAKE_ORGANIZATION_ID'
$missing = $required | Where-Object { -not $envVars[$_] }
if ($missing) {
    Write-Host "KO: missing or empty in .env: $($missing -join ', ')" -ForegroundColor Red
    exit 1
}

$token   = $envVars['MAKE_API_TOKEN']
$baseUrl = $envVars['MAKE_API_BASE_URL'].TrimEnd('/')
$orgId   = $envVars['MAKE_ORGANIZATION_ID']
$headers = @{ Authorization = "Token $token" }

# Test 1: token validity
Write-Host "[1/2] Checking token against $baseUrl/users/me ..."
try {
    $me = Invoke-RestMethod -Uri "$baseUrl/users/me" -Headers $headers -Method Get
    $userLabel = if ($me.users) { "$($me.users.name) <$($me.users.email)>" } else { 'unknown user' }
    Write-Host "      OK - token valid (user: $userLabel)" -ForegroundColor Green
} catch {
    Write-Host "      KO - token rejected ($($_.Exception.Message))" -ForegroundColor Red
    exit 1
}

# Test 2: organization access
Write-Host "[2/2] Checking organization $orgId ..."
try {
    $org = Invoke-RestMethod -Uri "$baseUrl/organizations/$orgId" -Headers $headers -Method Get
    $orgName = if ($org.organization) { $org.organization.name } else { 'unknown name' }
    Write-Host "      OK - organization accessible (name: $orgName)" -ForegroundColor Green
} catch {
    Write-Host "      KO - cannot access organization $orgId ($($_.Exception.Message))" -ForegroundColor Red
    exit 1
}

Write-Host "`nAll checks passed." -ForegroundColor Green
