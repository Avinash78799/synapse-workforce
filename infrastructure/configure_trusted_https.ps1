<# Configure locally trusted development HTTPS; run as the Windows desktop user. #>
[CmdletBinding()]
param(
    [string]$MkcertPath = 'mkcert',
    [string]$CaRoot = (Join-Path $env:LOCALAPPDATA 'mkcert')
)
$ErrorActionPreference = 'Stop'

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$FailureMessage
    )
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevPref
    if ($exitCode -ne 0) {
        if ($FailureMessage) {
            throw "$FailureMessage (exit code $exitCode)"
        } else {
            throw "Command failed: $FilePath $($Arguments -join ' ') (exit code $exitCode)"
        }
    }
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$certDir = Join-Path $repoRoot '.secrets\certs'
$caPath = [IO.Path]::GetFullPath($CaRoot)
if ($caPath.Equals($certDir,[StringComparison]::OrdinalIgnoreCase) -or
    $caPath.StartsWith($certDir+'\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'The CA directory must be outside the proxy certificate mount.'
}
$mkcert = (Get-Command $MkcertPath -ErrorAction Stop).Source
$oldCaRoot = $env:CAROOT
$oldTrustStores = $env:TRUST_STORES
Push-Location $repoRoot
try {
    # Check Docker before changing trust or replacing the existing certificate.
    Invoke-CheckedNative -FilePath 'docker' -Arguments @('compose','exec','-T','proxy','nginx','-t') -FailureMessage 'Docker/proxy access is required. Run from your own Windows terminal with Docker Desktop running.'
    $env:CAROOT = $caPath
    $env:TRUST_STORES = 'system'
    Invoke-CheckedNative -FilePath $mkcert -Arguments @('-install') -FailureMessage 'Local CA installation did not complete.'
    $pem = [IO.File]::ReadAllText((Join-Path $caPath 'rootCA.pem'))
    $base64 = $pem.Replace('-----BEGIN CERTIFICATE-----','').Replace('-----END CERTIFICATE-----','') -replace '\s',''
    $rootCert = [Security.Cryptography.X509Certificates.X509Certificate2]::new([Convert]::FromBase64String($base64))
    if (-not (Test-Path ('Cert:\CurrentUser\Root\'+$rootCert.Thumbprint)) -and
        -not (Test-Path ('Cert:\LocalMachine\Root\'+$rootCert.Thumbprint))) {
        throw 'The generated CA is not present in a Windows trusted root store.'
    }
    Write-Output ('Trusted development CA thumbprint: '+$rootCert.Thumbprint)
    $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
    $pending = Join-Path $repoRoot ('.secrets\cert-pending\'+$stamp)
    $backup = Join-Path $repoRoot ('.secrets\cert-backups\'+$stamp)
    New-Item -ItemType Directory -Path $pending,$backup,$certDir -Force | Out-Null
    Invoke-CheckedNative -FilePath $mkcert -Arguments @('-cert-file',(Join-Path $pending 'cert.pem'),'-key-file',(Join-Path $pending 'key.pem'),'localhost','127.0.0.1','::1') -FailureMessage 'Certificate generation failed; existing certificate is unchanged.'
    foreach ($name in @('cert.pem','key.pem')) {
        $current = Join-Path $certDir $name
        if (-not (Test-Path -LiteralPath $current)) { throw 'Expected the existing certificate and key before replacement.' }
        Copy-Item -LiteralPath $current -Destination (Join-Path $backup $name)
    }
    try {
        foreach ($name in @('cert.pem','key.pem')) {
            Copy-Item -LiteralPath (Join-Path $pending $name) -Destination (Join-Path $certDir $name) -Force
        }
        Invoke-CheckedNative -FilePath 'docker' -Arguments @('compose','exec','-T','proxy','nginx','-t') -FailureMessage 'Nginx rejected the replacement certificate.'
        Invoke-CheckedNative -FilePath 'docker' -Arguments @('compose','exec','-T','proxy','nginx','-s','reload') -FailureMessage 'Nginx reload failed.'
        $healthy = $false
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try {
                $response = Invoke-WebRequest -Uri 'https://localhost/api/health' -UseBasicParsing -TimeoutSec 10
                if ($response.StatusCode -eq 200) { $healthy = $true; break }
            } catch {
                if ($attempt -eq 4) { throw }
                Start-Sleep -Seconds 1
            }
        }
        if (-not $healthy) { throw 'Trusted HTTPS health verification failed.' }
    } catch {
        $setupFailure = $_
        foreach ($name in @('cert.pem','key.pem')) {
            Copy-Item -LiteralPath (Join-Path $backup $name) -Destination (Join-Path $certDir $name) -Force
        }
        try {
            Invoke-CheckedNative -FilePath 'docker' -Arguments @('compose','exec','-T','proxy','nginx','-t') -FailureMessage 'Nginx rollback validation failed.'
            Invoke-CheckedNative -FilePath 'docker' -Arguments @('compose','exec','-T','proxy','nginx','-s','reload') -FailureMessage 'Nginx rollback reload failed.'
        } catch {
            Write-Warning ('The old certificate files were restored, but Nginx rollback needs attention: ' + $_.Exception.Message)
        }
        throw $setupFailure
    }
    Write-Output ('Certificate backup: '+$backup)
    Write-Output 'Trusted HTTPS health check passed. Open https://localhost for browser verification.'
} finally {
    $env:CAROOT = $oldCaRoot
    $env:TRUST_STORES = $oldTrustStores
    Pop-Location
}

