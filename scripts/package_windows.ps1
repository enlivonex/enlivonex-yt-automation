param(
    [string]$Python = "python",
    [string]$OAuthClientSecretPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$OAuthSource = $null
$OAuthConfig = $null
$InstalledClient = $null
if ($OAuthClientSecretPath) {
    $OAuthSource = (Resolve-Path -LiteralPath $OAuthClientSecretPath).Path
    $ResolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd("\") + "\"
    if ($OAuthSource.StartsWith($ResolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Keep the OAuth client file outside the project folder and pass its local path to the build."
    }

    $OAuthConfig = Get-Content -LiteralPath $OAuthSource -Raw | ConvertFrom-Json
    $InstalledClient = $OAuthConfig.installed
    if (
        -not $InstalledClient -or
        [string]::IsNullOrWhiteSpace($InstalledClient.client_id) -or
        [string]::IsNullOrWhiteSpace($InstalledClient.client_secret)
    ) {
        throw "The supplied OAuth file is not a valid Desktop app client configuration."
    }
}

& $Python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install the packaging requirements." }

# PyInstaller output is rebuilt each time; no project data folder is copied.
if (Test-Path -LiteralPath (Join-Path $Root "build\Enlivonex")) {
    Remove-Item -LiteralPath (Join-Path $Root "build\Enlivonex") -Recurse -Force
}
if (Test-Path -LiteralPath (Join-Path $Root "dist\Enlivonex-Windows")) {
    Remove-Item -LiteralPath (Join-Path $Root "dist\Enlivonex-Windows") -Recurse -Force
}
& $Python -m PyInstaller --noconfirm --clean enlivonex.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not build Enlivonex." }

$Package = Join-Path $Root "dist\Enlivonex-Windows"
$OAuthClientSecretIncluded = $false
if ($OAuthClientSecretPath) {
    $DataDirectory = Join-Path $Package "data"
    New-Item -ItemType Directory -Path $DataDirectory -Force | Out-Null
    Copy-Item -LiteralPath $OAuthSource `
        -Destination (Join-Path $DataDirectory "youtube_client_secret.json")
    $OAuthClientSecretIncluded = $true
}

$OAuthPackageNote = if ($OAuthClientSecretIncluded) {
    "This package includes the publisher's Google Desktop app OAuth client configuration for installed-app sign-in. Desktop apps are public OAuth clients; anyone who receives this ZIP can extract the client ID and client_secret, which are not confidential credentials. Never use them as server credentials or include user OAuth tokens, .env, or AI provider API keys."
} else {
    "OAuth client configuration is not included. Supply -OAuthClientSecretPath when building a ZIP that needs Google Desktop app sign-in."
}
@"
Enlivonex YouTube Automation - testing build

Run Enlivonex.exe. The first run creates local settings and data folders next
to the application. In AI settings, scan the PC, refresh local Ollama models,
choose a story model and an embedding model, and install any missing runtime
components. Model downloads can be several gigabytes and require your approval
through the Install missing components button.

This distribution does not contain your .env, AI provider API keys, YouTube
account tokens, database, logs, source documents, or generated videos.
Keep this folder in a location writable by the Windows account running it.

Imported local or Google Drive for desktop videos are copied into data\clips
beside the executable; the original footage is not moved or deleted. Direct
cloud Drive imports require separate Google Drive OAuth configuration and any
Google verification required for the Drive scope.

$OAuthPackageNote
"@ | Set-Content -LiteralPath (Join-Path $Package "README-TESTING.txt") -Encoding UTF8

$Archive = Join-Path $Root "dist\Enlivonex-Windows.zip"
if (Test-Path -LiteralPath $Archive) { Remove-Item -LiteralPath $Archive -Force }
Compress-Archive -Path (Join-Path $Package "*") -DestinationPath $Archive
Write-Host "Created $Archive"
