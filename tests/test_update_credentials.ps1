# Run with Windows PowerShell 5.1. Uses only fake credentials and a fake SSH client.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root "scripts/server_update_credentials.ps1")
$directory = Join-Path ([IO.Path]::GetTempPath()) ("icewag-credentials-test-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $directory > $null
$oldPath = $env:PATH
$envNames = @("SSH_ASKPASS", "SSH_ASKPASS_REQUIRE", "ICEWAG_ASKPASS_SCRIPT", "ICEWAG_UPDATE_CREDENTIAL_FILE", "ICEWAG_ASKPASS_PROMPT", "SSH_ASKPASS_PROMPT")
$oldEnvironment = @{}
foreach ($name in $envNames) {
    $oldEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    [Environment]::SetEnvironmentVariable($name, $null, "Process")
}

function Assert-Test([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

try {
    $passwordText = 'fake-$-"-&-password-' + [Guid]::NewGuid().ToString("N")
    $secure = ConvertTo-SecureString $passwordText -AsPlainText -Force
    $settings = [pscustomobject]@{
        Target = "test@example.invalid"; Port = 2222; RemotePath = "~/iceWaG-bot"
        Branch = "main"; IdentityFile = ""; WithWatchdog = $false
    }
    Save-UpdateProfile $directory $settings $secure
    $credentialPath = Join-Path $directory "credential.clixml"
    Assert-Test (-not ([IO.File]::ReadAllText($credentialPath).Contains($passwordText))) "Password was saved in plaintext."
    $record = Import-Clixml -LiteralPath $credentialPath
    Assert-Test ($record.Credential.GetNetworkCredential().Password -ceq $passwordText) "DPAPI round trip failed."
    Assert-Test ((Get-UpdateProfile $directory).Target -eq $settings.Target) "Saved IP/username did not load."
    Write-Host "PASS: connection saved; password encrypted and decryptable by this Windows user"

    Assert-Test (Test-UpdateSavedPassword $directory $settings.Target 2222) "Matching connection was rejected."
    Assert-Test (-not (Test-UpdateSavedPassword $directory "other@example.invalid" 2222)) "Password reused for a different host."
    Assert-Test (-not (Test-UpdateSavedPassword $directory $settings.Target 2223)) "Password reused for a different port."
    Write-Host "PASS: saved password bound to host and port"

    $helper = Initialize-UpdateAskpass $directory
    $env:ICEWAG_UPDATE_CREDENTIAL_FILE = $credentialPath
    $env:ICEWAG_ASKPASS_SCRIPT = Join-Path $root "scripts/server_update_askpass.ps1"
    $answer = & $helper "test@example.invalid's password:"
    Assert-Test ($LASTEXITCODE -eq 0 -and $answer -ceq $passwordText) "Native askpass pipe did not return the password correctly."
    $answer = & $helper 'Enter passphrase for key test:'
    Assert-Test ($LASTEXITCODE -ne 0 -and -not $answer) "Server password sent as private-key passphrase."
    $answer = & $helper 'Verification code:'
    Assert-Test ($LASTEXITCODE -ne 0 -and -not $answer) "Server password sent as OTP."
    Write-Host "PASS: native askpass delivery works; private-key and OTP prompts rejected"

    # The native stub never connects to any host. Test the real launcher wiring.
    $stub = @'
using System;
class FakeSsh {
    static int Main(string[] args) {
        Console.WriteLine("CREDENTIAL_MODE=" + (String.IsNullOrEmpty(Environment.GetEnvironmentVariable("SSH_ASKPASS")) ? "interactive" : "saved"));
        foreach (var arg in args) Console.WriteLine("ARG=" + arg);
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $stub -OutputAssembly (Join-Path $directory "ssh.exe") -OutputType ConsoleApplication -IgnoreWarnings -WarningAction SilentlyContinue
    $env:PATH = $directory + ";" + $oldPath
    $launcher = Join-Path $root "scripts/update_server.ps1"
    $output = & $launcher -ConfigDirectory $directory *>&1
    Assert-Test ($LASTEXITCODE -eq 0 -and $output -contains "CREDENTIAL_MODE=saved") "Launcher did not use the saved credential."
    Assert-Test ($output -contains "ARG=test@example.invalid" -and $output -contains "ARG=2222") "Launcher did not use saved host/port."
    Assert-Test ($output -contains "ARG=StrictHostKeyChecking=ask") "Host key verification was disabled."
    Assert-Test (-not (($output | Out-String).Contains($passwordText))) "Password leaked into command arguments/output."
    Assert-Test (-not $env:SSH_ASKPASS) "Askpass environment was not restored."
    $output = & $launcher -ConfigDirectory $directory -Target "other@example.invalid" *>&1
    Assert-Test ($LASTEXITCODE -eq 0 -and $output -contains "CREDENTIAL_MODE=interactive") "Host override reused saved password."
    $output = & $launcher -ConfigDirectory $directory -Port 2223 *>&1
    Assert-Test ($LASTEXITCODE -eq 0 -and $output -contains "CREDENTIAL_MODE=interactive") "Port override reused saved password."
    Write-Host "PASS: launcher loads settings, protects host verification, isolates password and restores environment"

    $emptyPassword = [Security.SecureString]::new()
    Save-UpdateProfile $directory $settings $emptyPassword
    Assert-Test (-not (Test-Path -LiteralPath $credentialPath)) "Blank password did not remove stored credential."
    Assert-Test (-not (Test-UpdateSavedPassword $directory $settings.Target 2222)) "Removed password still selected."
    Write-Host "PASS: blank password removes stored credential"

    # Exercise the first-run UI without opening an interactive prompt or connecting.
    function Read-Host {
        param([string]$Prompt, [switch]$AsSecureString)
        if ($AsSecureString) { return [Security.SecureString]::new() }
        return ""
    }
    $output = & $launcher -Configure -ConfigDirectory $directory -Target "test@example.invalid" -Port 0 *>&1
    Assert-Test ($LASTEXITCODE -eq 0) "First-run configuration failed."
    Assert-Test ((Get-UpdateProfile $directory).Port -eq 0) "Default SSH port was not saved."
    Assert-Test (-not (($output | Out-String).Contains("CREDENTIAL_MODE="))) "Configuration unexpectedly connected to SSH."
    $output = & $launcher -ConfigDirectory $directory *>&1
    Assert-Test ($LASTEXITCODE -eq 0 -and $output -contains "CREDENTIAL_MODE=interactive") "Default-port profile did not load."
    Assert-Test (-not ($output -contains "ARG=-p")) "SSH config port was overridden."
    Write-Host "PASS: setup only saves settings; default SSH port remains configurable"
} finally {
    $env:PATH = $oldPath
    foreach ($name in $oldEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $oldEnvironment[$name], "Process")
    }
    if ($secure) { $secure.Dispose() }
    if ($emptyPassword) { $emptyPassword.Dispose() }
    # Delete only the unique test directory verified inside the OS temp directory.
    $resolved = [IO.Path]::GetFullPath($directory)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and
        [IO.Path]::GetFileName($resolved).StartsWith("icewag-credentials-test-")) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
