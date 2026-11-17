param(
    [Parameter(Mandatory)][string]$ExpectedAccountId,
    [string]$Profile = 'rucma-sponsored',
    [string]$SshHost = 'lab',
    [string]$AliyunPath = (Join-Path $env:LOCALAPPDATA 'AliyunCLI\aliyun.exe')
)
$ErrorActionPreference = 'Stop'
# Refresh OAuth/STS using the local CLI. Never send OAuth refresh tokens to Linux.
$identityText = & $AliyunPath sts GetCallerIdentity --profile $Profile --region cn-beijing 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Aliyun CLI authentication failed; authorize this profile locally first.' }
$identity = ($identityText -join "`n") | ConvertFrom-Json
if ($identity.AccountId -ne $ExpectedAccountId -or $identity.IdentityType -ne 'RAMUser') {
    throw 'Unexpected Aliyun account; refusing to synchronize credentials.'
}
$configuration = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.aliyun\config.json') -Raw | ConvertFrom-Json
$entry = @($configuration.profiles | Where-Object name -eq $Profile)
if ($entry.Count -ne 1 -or !$entry[0].sts_token) { throw 'A unique profile with temporary STS credentials is required.' }
$value = $entry[0]
$payload = @{
    access_key_id = $value.access_key_id
    access_key_secret = $value.access_key_secret
    session_token = $value.sts_token
    expires_at = [long]$value.sts_expiration
} | ConvertTo-Json -Compress
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = 'ssh.exe'
$start.UseShellExecute = $false
$start.RedirectStandardInput = $true
$start.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
foreach ($argument in @('-T', '-o', 'BatchMode=yes', $SshHost, 'python3 ~/proj/eventyay/deployment/install_oss_credentials.py')) {
    $start.ArgumentList.Add($argument)
}
$process = [Diagnostics.Process]::Start($start)
try {
    $process.StandardInput.Write($payload)
    $process.StandardInput.Close()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw 'The server refused the credential update.' }
} finally {
    $payload = $null
    $configuration = $null
    $entry = $null
    $value = $null
    $process.Dispose()
}
Write-Output 'OSS temporary credentials synchronized. The website reloads the private file automatically.'
