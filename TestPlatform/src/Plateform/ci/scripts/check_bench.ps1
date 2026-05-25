# check_bench.ps1  -  Vérification connectivité banc WipeWash (Windows)
# ============================================================
# Appelé par Jenkins au stage "Bench Connectivity"
#
# Vérifie :
#   [1] Ping RPiBCM
#   [2] Ping RPiSIM
#   [3] Redis TCP + PONG
#   [4] Port 5000 RPiBCM (bcm_tcp_broadcast - Motor/Vehicle RX)
#   [5] Port 5555 RPiSIM (crslin.py - LIN TX/RX)
#   [6] Port 5556 RPiBCM (bcm_tcp_pump - Pump data)
#   [7] Port 5557 RPiSIM (bcm_tcp_can.py - CAN frames RX)
#   [8] Port 5002 RPiSIM (bcmcan.py - CAN TX commandes)
#
# Critiques (exit 2 si absent)  : Redis, port 5000 BCM
# Non-bloquants (WARN seulement): Ping, ports SIM
#
# Usage :
#   powershell -File ci\scripts\check_bench.ps1 -BcmHost 10.20.0.25 -SimHost 10.20.0.7
#   → exit 0 : banc OK
#   → exit 2 : service critique manquant (Redis ou port 5000)
# ============================================================

param(
    [string]$BcmHost   = "10.20.0.25",
    [string]$SimHost   = "10.20.0.7",
    [int]   $RedisPort = 6379,
    [string]$Python    = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Continue"
$criticalOk = $true    # services bloquants (Redis + port BCM)
$warnings   = @()      # services non-bloquants

# ── Helper : test TCP port ────────────────────────────────────────────────────
function Test-TCPPort {
    param(
        [string]$TargetHost,
        [int]   $Port,
        [string]$Label,
        [int]   $TimeoutMs = 2000
    )
    $tcp   = New-Object System.Net.Sockets.TcpClient
    $async = $tcp.BeginConnect($TargetHost, $Port, $null, $null)
    $wait  = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
    $ok    = $false
    if ($wait) {
        try { $tcp.EndConnect($async); $ok = $tcp.Connected } catch {}
    }
    $tcp.Close()
    if ($ok) {
        Write-Host "  [OK]   $Label  ($TargetHost`:$Port)"
    } else {
        Write-Host "  [FAIL] $Label  ($TargetHost`:$Port)"
    }
    return $ok
}

# ── Bannière ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================"
Write-Host "  WipeWash Bench Connectivity Check"
Write-Host "  BCM  : $BcmHost"
Write-Host "  SIM  : $SimHost"
Write-Host "  Redis: $BcmHost`:$RedisPort"
Write-Host "============================================================"
Write-Host ""

# ── [1] Ping RPiBCM ───────────────────────────────────────────────────────────
Write-Host "[1] Ping RPiBCM ($BcmHost)..."
if (Test-Connection -ComputerName $BcmHost -Count 2 -Quiet -ErrorAction SilentlyContinue) {
    Write-Host "  [OK]   Ping RPiBCM"
} else {
    Write-Warning "  [WARN] Ping RPiBCM echoue (l'ICMP peut etre bloque - non bloquant)"
    $warnings += "Ping BCM"
}

# ── [2] Ping RPiSIM ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "[2] Ping RPiSIM ($SimHost)..."
if (Test-Connection -ComputerName $SimHost -Count 2 -Quiet -ErrorAction SilentlyContinue) {
    Write-Host "  [OK]   Ping RPiSIM"
} else {
    Write-Warning "  [WARN] Ping RPiSIM echoue (non bloquant)"
    $warnings += "Ping SIM"
}

# ── [3] Redis TCP + PONG ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "[3] Redis $BcmHost`:$RedisPort..."
$redisTcpOk = Test-TCPPort -TargetHost $BcmHost -Port $RedisPort -Label "Redis TCP"
if ($redisTcpOk) {
    # Verification PONG Redis via Python
    # Arguments passes en CLI pour eviter les problemes d interpolation PS dans le heredoc
    $redisScript = @"
import sys, redis
host = sys.argv[1]
port = int(sys.argv[2])
try:
    r = redis.Redis(host, port, socket_connect_timeout=3)
    r.ping()
    print('  [OK]   Redis PONG recu')
    sys.exit(0)
except ImportError:
    print('  [WARN] Module redis non installe - test PONG ignore')
    sys.exit(0)
except Exception as e:
    print('  [FAIL] Redis PONG: ' + str(e))
    sys.exit(1)
"@
    $result = & $Python -c $redisScript $BcmHost $RedisPort 2>&1
    Write-Host $result
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "  Redis TCP ouvert mais PONG echoue - bcm_rte.py tourne-t-il ?"
        $criticalOk = $false
    }
} else {
    Write-Warning "  Redis inaccessible - bcm_rte.py doit tourner sur RPiBCM"
    $criticalOk = $false
}

# ── [4] Port 5000 RPiBCM (bcm_tcp_broadcast - source Motor/Vehicle data) ─────
Write-Host ""
Write-Host "[4] Port 5000 RPiBCM (bcm_tcp_broadcast)..."
if (-not (Test-TCPPort -TargetHost $BcmHost -Port 5000 -Label "BCM broadcast TCP")) {
    Write-Warning "  bcm_tcp_broadcast.py ne semble pas actif sur RPiBCM (port 5000)"
    Write-Warning "  Les workers Motor et Pump ne recevront pas de donnees"
    $criticalOk = $false
}

# ── [5] Port 5555 RPiSIM (crslin.py - LIN bidirectionnel) ────────────────────
Write-Host ""
Write-Host "[5] Port 5555 RPiSIM (crslin.py LIN)..."
if (-not (Test-TCPPort -TargetHost $SimHost -Port 5555 -Label "SIM LIN TCP")) {
    Write-Warning "  crslin.py ne semble pas actif sur RPiSIM (port 5555) - non bloquant"
    $warnings += "Port 5555 SIM"
}

# ── [6] Port 5556 RPiBCM (bcm_tcp_pump - données pompe) ──────────────────────
Write-Host ""
Write-Host "[6] Port 5556 RPiBCM (bcm_tcp_pump)..."
if (-not (Test-TCPPort -TargetHost $BcmHost -Port 5556 -Label "BCM pump TCP")) {
    Write-Warning "  bcm_tcp_pump.py ne semble pas actif (port 5556) - non bloquant"
    $warnings += "Port 5556 BCM"
}

# ── [7] Port 5557 RPiSIM (bcm_tcp_can.py - CAN frames RX) ────────────────────
Write-Host ""
Write-Host "[7] Port 5557 RPiSIM (bcm_tcp_can.py CAN RX)..."
if (-not (Test-TCPPort -TargetHost $SimHost -Port 5557 -Label "SIM CAN RX TCP")) {
    Write-Warning "  bcm_tcp_can.py ne semble pas actif sur RPiSIM (port 5557) - non bloquant"
    $warnings += "Port 5557 SIM"
}

# ── [8] Port 5002 RPiSIM (bcmcan.py - CAN TX commandes) ──────────────────────
Write-Host ""
Write-Host "[8] Port 5002 RPiSIM (bcmcan.py CAN TX)..."
if (-not (Test-TCPPort -TargetHost $SimHost -Port 5002 -Label "SIM CAN TX TCP")) {
    Write-Warning "  bcmcan.py ne semble pas actif sur RPiSIM (port 5002) - non bloquant"
    $warnings += "Port 5002 SIM"
}

# ── Résumé ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================================"
if ($criticalOk) {
    Write-Host "  RESULTAT : BANC OK - services critiques (Redis + BCM:5000) repondent"
    if ($warnings.Count -gt 0) {
        Write-Host "  Avertissements non-bloquants : $($warnings -join ', ')"
        Write-Host "  → Certains tests utilisant ces ports peuvent TIMEOUT"
    }
    Write-Host "============================================================"
    Write-Host ""
    exit 0
} else {
    Write-Host "  RESULTAT : BANC INCOMPLET - services critiques manquants"
    Write-Host ""
    Write-Host "  Checklist RPiBCM ($BcmHost) :"
    Write-Host "    1. sudo systemctl status redis     (ou redis-server)"
    Write-Host "    2. python3 bcm_rte.py              (publie dans Redis)"
    Write-Host "    3. python3 bcm_tcp_broadcast.py    (port 5000)"
    Write-Host ""
    Write-Host "  Checklist RPiSIM ($SimHost) :"
    Write-Host "    4. python3 crslin.py               (port 5555)"
    Write-Host "    5. python3 bcmcan.py               (ports 5557 + 5002)"
    Write-Host "============================================================"
    Write-Host ""
    exit 2
}
