# Compares raw connection throughput against throughput through the VPN
# tunnel (whichever provider is configured in .env). Puts NOTHING into a
# torrent swarm -- your IP is never exposed to peers. This is the safe way
# to answer "what is the VPN costing me?".
#
# Usage:
#   .\speedtest.ps1            # 25 MB per path (default)
#   .\speedtest.ps1 -SizeMB 100   # more accurate, uses more of your data cap
#
# NOTE: the tunnelled run consumes your VPN provider's data allowance, if it
# has one (Privado's free tier is 10 GB/month). The direct run does not.

param(
    [int]$SizeMB = 25,
    [int]$Runs = 2
)

$bytes = $SizeMB * 1000000
$url   = "https://speed.cloudflare.com/__down?bytes=$bytes"

function Format-Speed([double]$bytesPerSec) {
    "{0,6:N1} MB/s  ({1,4:N0} Mbps)" -f ($bytesPerSec / 1MB), ($bytesPerSec * 8 / 1MB)
}

Write-Host ""
Write-Host "Speed test: $SizeMB MB x $Runs run(s) per path" -ForegroundColor Cyan
Write-Host "Tunnelled runs use ~$($SizeMB * $Runs) MB of your VPN provider's allowance, if it has one." -ForegroundColor DarkGray
Write-Host ""

# --- direct -------------------------------------------------------------
# Uses curl.exe (built into Windows 10/11), NOT Invoke-WebRequest. Under
# Windows PowerShell 5.1 the latter's progress rendering throttles downloads to
# ~1 MB/s, which made this test report the VPN as *faster* than the raw line.
# Both paths must use curl or the comparison is meaningless.
$directResults = @()
for ($i = 1; $i -le $Runs; $i++) {
    Write-Host "  direct  run $i ..." -NoNewline
    $raw = curl.exe -s -o NUL -w "%{speed_download}" --max-time 180 $url 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw -and [double]$raw -gt 0) {
        $bps = [double]$raw
        $directResults += $bps
        Write-Host ("  " + (Format-Speed $bps))
    } else {
        Write-Host "  FAILED" -ForegroundColor Red
    }
}

# --- through the tunnel -------------------------------------------------
$vpnResults = @()
$running = (docker inspect -f '{{.State.Running}}' qbittorrent 2>$null)
if ($running -ne 'true') {
    Write-Host "  qbittorrent is not running - skipping tunnelled test" -ForegroundColor Yellow
} else {
    for ($i = 1; $i -le $Runs; $i++) {
        Write-Host "  tunnel  run $i ..." -NoNewline
        $raw = docker exec qbittorrent sh -c "curl -s -o /dev/null -w '%{speed_download}' --max-time 180 '$url'" 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw -and [double]$raw -gt 0) {
            $bps = [double]$raw
            $vpnResults += $bps
            Write-Host ("  " + (Format-Speed $bps))
        } else {
            Write-Host "  FAILED (is the tunnel up? .\vpn-toggle.ps1 status)" -ForegroundColor Red
        }
    }
}

# --- summary ------------------------------------------------------------
Write-Host ""
Write-Host "== Result ==" -ForegroundColor Cyan
if ($directResults.Count -gt 0) {
    $d = ($directResults | Measure-Object -Maximum).Maximum
    Write-Host ("  direct (no VPN)   " + (Format-Speed $d))
}
if ($vpnResults.Count -gt 0) {
    $v = ($vpnResults | Measure-Object -Maximum).Maximum
    Write-Host ("  through the VPN   " + (Format-Speed $v))
}
if ($directResults.Count -gt 0 -and $vpnResults.Count -gt 0) {
    $pct = (1 - ($v / $d)) * 100
    Write-Host ("  tunnel overhead   {0,5:N0}%" -f $pct)
    Write-Host ""
    Write-Host "Best of $Runs run(s) shown. Short transfers under-report because of TCP" -ForegroundColor DarkGray
    Write-Host "slow-start - use -SizeMB 100 if you want a firmer number." -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "Reality check: torrent speed is governed by seeder count and the lack" -ForegroundColor DarkGray
    Write-Host "of port forwarding far more than by this ceiling." -ForegroundColor DarkGray
}
Write-Host ""
