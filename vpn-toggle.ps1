# Pause / resume the Privado tunnel without recreating containers.
#
# IMPORTANT -- what "stopped" actually means:
#   Stopping the tunnel does NOT let qBittorrent connect directly. gluetun's
#   firewall stays up, so qBittorrent simply loses ALL connectivity. Verified:
#   with the tunnel stopped, `curl` from inside qbittorrent returns nothing.
#   This is a PAUSE button (stop all torrent traffic now), not a VPN bypass.
#
# Usage:
#   .\vpn-toggle.ps1            # toggle: running <-> stopped
#   .\vpn-toggle.ps1 status
#   .\vpn-toggle.ps1 stop
#   .\vpn-toggle.ps1 start

param(
    [ValidateSet("toggle", "status", "stop", "start")]
    [string]$Action = "toggle"
)

$ErrorActionPreference = "Stop"

# Single source of truth for the key: the auth config gluetun itself reads.
$authFile = Join-Path $PSScriptRoot "gluetun-auth.toml"
if (-not (Test-Path $authFile)) { Write-Host "gluetun-auth.toml not found" -ForegroundColor Red; exit 1 }
$m = Select-String -Path $authFile -Pattern '^\s*apikey\s*=\s*"(.+)"' | Select-Object -First 1
if (-not $m) { Write-Host "no apikey found in gluetun-auth.toml" -ForegroundColor Red; exit 1 }

$headers = @{ "X-API-Key" = $m.Matches.Groups[1].Value }
$base    = "http://localhost:8010/v1"

function Get-VpnStatus {
    try { (Invoke-RestMethod "$base/vpn/status" -Headers $headers -TimeoutSec 10).status }
    catch { Write-Host "Cannot reach gluetun on :8010 - is the stack up?" -ForegroundColor Red; exit 1 }
}

function Set-VpnStatus([string]$desired) {
    $body = "{`"status`":`"$desired`"}"
    $r = Invoke-RestMethod "$base/vpn/status" -Method Put -Headers $headers -Body $body -ContentType "application/json" -TimeoutSec 30
    return $r.outcome
}

$current = Get-VpnStatus

switch ($Action) {
    "status" {
        Write-Host "VPN tunnel: $current" -ForegroundColor Cyan
        if ($current -eq "running") {
            try {
                $ip = Invoke-RestMethod "$base/publicip/ip" -Headers $headers -TimeoutSec 15
                Write-Host "Exit node : $($ip.public_ip)  ($($ip.city), $($ip.country))"
                Write-Host "Note: GeoIP city is often wrong on Privado ranges - trust the IP, not the city." -ForegroundColor DarkGray
            } catch { Write-Host "(public IP not available yet)" -ForegroundColor Yellow }
        } else {
            Write-Host "qBittorrent currently has NO internet access." -ForegroundColor Yellow
        }
        exit 0
    }
    "stop"   { $target = "stopped" }
    "start"  { $target = "running" }
    "toggle" { $target = if ($current -eq "running") { "stopped" } else { "running" } }
}

if ($current -eq $target) {
    Write-Host "VPN is already '$current' - nothing to do." -ForegroundColor Yellow
    exit 0
}

Write-Host "VPN: $current -> $target ..." -NoNewline
$outcome = Set-VpnStatus $target
Write-Host " $outcome"

if ($target -eq "stopped") {
    Write-Host ""
    Write-Host "qBittorrent now has NO internet access (firewall still up)." -ForegroundColor Yellow
    Write-Host "This is a pause, NOT a bypass - torrents will stall until you start it again." -ForegroundColor Yellow
    Write-Host "Resume with: .\vpn-toggle.ps1 start" -ForegroundColor Cyan
} else {
    Write-Host "Waiting for the tunnel to come up..." -NoNewline
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 5
        try {
            $ip = Invoke-RestMethod "$base/publicip/ip" -Headers $headers -TimeoutSec 10
            Write-Host ""
            Write-Host "Connected. Exit node: $($ip.public_ip)" -ForegroundColor Green
            $real = (Invoke-RestMethod https://icanhazip.com -TimeoutSec 10).Trim()
            if ($ip.public_ip -eq $real) {
                Write-Host "WARNING: exit IP equals your real IP - do not download." -ForegroundColor Red
            } else {
                Write-Host "Your real IP ($real) is not exposed." -ForegroundColor Green
            }
            exit 0
        } catch { Write-Host "." -NoNewline }
    }
    Write-Host ""
    Write-Host "Tunnel did not report an IP in 60s - check: docker logs gluetun" -ForegroundColor Yellow
}
