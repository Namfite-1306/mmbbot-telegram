param(
    [string]$Python = 'python'
)

$projectDirectory = Split-Path -Parent $PSScriptRoot
$retrySeconds = 15
Push-Location -LiteralPath $projectDirectory
try {
    while ($true) {
        & $Python -m app.main
        $result = $LASTEXITCODE
        if ($result -eq 0) {
            break
        }
        if ($result -ne 4) {
            Write-Error "Bot stopped with exit code $result. Check configuration/logs before restarting."
            exit $result
        }
        Write-Warning "Telegram connection failed. Retrying bot startup in $retrySeconds seconds."
        Start-Sleep -Seconds $retrySeconds
        $retrySeconds = [Math]::Min(300, $retrySeconds * 2)
    }
}
finally {
    Pop-Location
}
