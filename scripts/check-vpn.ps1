# Verifies that qBittorrent's traffic actually leaves through the VPN,
# and that the kill switch works. Run this after every config change.

# Safety net: docker-compose.novpn.yml runs the SAME container names with no
# VPN at all -- if that file is what's currently up (not docker-compose.yml),
# qbittorrent exists but gluetun does not. Say so loudly before the rest of
# this script runs, since steps 1-3 below will otherwise just look like a
# leak without explaining why.
$qbitRunning = (docker inspect -f '{{.State.Running}}' qbittorrent 2>$null) -eq 'true'
$gluetunExists = $null -ne (docker inspect -f '{{.State.Running}}' gluetun 2>$null)
if ($qbitRunning -and -not $gluetunExists) {
    Write-Host ""
    Write-Host "  *********************************************************" -ForegroundColor Red
    Write-Host "  *  WARNING: running docker-compose.novpn.yml right now  *" -ForegroundColor Red
    Write-Host "  *  qBittorrent has NO VPN in this mode. Any torrent     *" -ForegroundColor Red
    Write-Host "  *  exposes your real IP to the swarm.                   *" -ForegroundColor Red
    Write-Host "  *                                                       *" -ForegroundColor Red
    Write-Host "  *  Switch back when you're done:                        *" -ForegroundColor Red
    Write-Host "  *    docker compose -f docker-compose.novpn.yml down    *" -ForegroundColor Red
    Write-Host "  *    docker compose up -d                               *" -ForegroundColor Red
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
$authFile = Join-Path (Split-Path $PSScriptRoot -Parent) "gluetun-auth.toml"
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
docker compose -f "$(Split-Path $PSScriptRoot -Parent)\docker-compose.yml" up -d | Out-Null
# qBittorrent AND Prowlarr both share gluetun's namespace, which died with it;
# both must be restarted to rejoin the new one. Missing prowlarr here was a
# real bug -- caught live: it silently sat on the dead namespace with no
# network path at all until this restart was added.
docker compose -f "$(Split-Path $PSScriptRoot -Parent)\docker-compose.yml" restart qbittorrent prowlarr | Out-Null
Write-Host "Done.`n"
