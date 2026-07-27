# Verifies that qBittorrent's traffic actually leaves through the VPN,
# and that the kill switch works. Run this after every config change.

# Safety net: the benchmarking instance from docker-compose.novpn.yml has no
# VPN at all. If it was left running, say so loudly -- it is easy to forget.
if ((docker inspect -f '{{.State.Running}}' qbittorrent-novpn 2>$null) -eq 'true') {
    Write-Host ""
    Write-Host "  *********************************************************" -ForegroundColor Red
    Write-Host "  *  WARNING: qbittorrent-novpn is RUNNING                *" -ForegroundColor Red
    Write-Host "  *  That instance has NO VPN. Any torrent in it exposes  *" -ForegroundColor Red
    Write-Host "  *  your real IP to the swarm.                           *" -ForegroundColor Red
    Write-Host "  *                                                       *" -ForegroundColor Red
    Write-Host "  *  Shut it down when finished benchmarking:             *" -ForegroundColor Red
    Write-Host "  *    docker compose -f docker-compose.novpn.yml down    *" -ForegroundColor Red
    Write-Host "  *********************************************************" -ForegroundColor Red
}

Write-Host "`n== 1. Your real IP (from Windows, no VPN) ==" -ForegroundColor Cyan
$real = (Invoke-RestMethod -Uri "https://ifconfig.me/ip").Trim()
Write-Host $real

Write-Host "`n== 2. IP that qBittorrent's container sees ==" -ForegroundColor Cyan
$vpn = (docker exec qbittorrent curl -s --max-time 15 https://ifconfig.me/ip)
Write-Host $vpn

Write-Host "`n== 3. Verdict ==" -ForegroundColor Cyan
if (-not $vpn) {
    Write-Host "FAIL: no response. Is the stack running? (docker compose ps)" -ForegroundColor Red
} elseif ($vpn.Trim() -eq $real) {
    Write-Host "LEAK: container is using your real IP. STOP and fix before torrenting." -ForegroundColor Red
} else {
    Write-Host "OK: traffic is going out via $($vpn.Trim()), not $real" -ForegroundColor Green
}

Write-Host "`n== 4. gluetun's reported public IP ==" -ForegroundColor Cyan
# The control server requires an API key. Read it from the auth config so there
# is a single source of truth -- rotate the key there, not here.
$authFile = Join-Path $PSScriptRoot "gluetun-auth.toml"
$apiKey = $null
if (Test-Path $authFile) {
    $apiKey = (Select-String -Path $authFile -Pattern '^\s*apikey\s*=\s*"(.+)"' | Select-Object -First 1).Matches.Groups[1].Value
}
if (-not $apiKey) {
    Write-Host "no API key found in gluetun-auth.toml - skipping" -ForegroundColor Yellow
} else {
    try { (Invoke-RestMethod -Uri "http://localhost:8010/v1/publicip/ip" -Headers @{"X-API-Key" = $apiKey}) | Format-List }
    catch { Write-Host "control server not reachable on :8010 (or key rejected)" -ForegroundColor Yellow }
}

Write-Host "`n== 5. Kill-switch test ==" -ForegroundColor Cyan
Write-Host "Stopping gluetun..." -NoNewline
docker stop gluetun | Out-Null
Write-Host " done."
# An empty curl result is NOT proof on its own -- the exec itself may have
# failed because the container went down with gluetun's namespace.
$state = (docker inspect -f '{{.State.Running}}' qbittorrent 2>$null)
if ($state -ne 'true') {
    Write-Host "INCONCLUSIVE: qbittorrent stopped along with gluetun. The kill switch" -ForegroundColor Yellow
    Write-Host "holds by construction (no namespace = no network), but wasn't exercised." -ForegroundColor Yellow
} else {
    $leaked = docker exec qbittorrent curl -s --max-time 8 https://ifconfig.me/ip 2>$null
    if ($LASTEXITCODE -eq 0 -and $leaked) {
        Write-Host "KILL SWITCH FAILED: still reachable as $leaked" -ForegroundColor Red
    } else {
        Write-Host "OK: qBittorrent has no connectivity without the tunnel." -ForegroundColor Green
    }
}
Write-Host "Restarting stack..."
docker compose -f "$PSScriptRoot\docker-compose.yml" up -d | Out-Null
# qBittorrent's namespace died with gluetun; it must be restarted to rejoin it.
docker compose -f "$PSScriptRoot\docker-compose.yml" restart qbittorrent | Out-Null
Write-Host "Done.`n"
