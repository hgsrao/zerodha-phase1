#!/usr/bin/env powershell
# ECS CALIBRATION LIVE MONITOR V2 - FIXED
# Dynamically detects calibration process instead of hard-coding PIDs
# Date: August 30, 2026 (audit-corrected)

param(
    [int]$RefreshSeconds = 30,
    [int]$MaxIterations = 1028,
    [string]$ProcessName = "python",
    [string]$ScriptName = "STAGE2_CALIBRATION_33PARAMS_24HOURS.py"
)

# ============================================================================
# CONFIGURATION
# ============================================================================

$LogFile = "calibration_errors.txt"
$ExpectedStartTime = Get-Date  # Will update after finding process

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

function Find-CalibrationProcess {
    """
    Dynamically locate the calibration process.
    Returns process object or $null if not found.
    """
    try {
        # Look for python.exe running the calibration script
        $processes = Get-Process $ProcessName -ErrorAction SilentlyContinue

        if (-not $processes) {
            return $null
        }

        # Return the most recent Python process (likely our calibration)
        $latest = $processes | Sort-Object StartTime -Descending | Select-Object -First 1
        return $latest
    }
    catch {
        return $null
    }
}

function Get-CalibrationStats {
    """
    Extract calibration metrics from log file.
    Returns hashtable with stats or $null if file doesn't exist.
    """
    if (-not (Test-Path $LogFile)) {
        return $null
    }

    try {
        $content = Get-Content $LogFile -Tail 100 -ErrorAction SilentlyContinue
        $lines = @($content) -split "`n"

        $stats = @{
            'TotalLines'      = $lines.Count
            'LastUpdate'      = (Get-Item $LogFile).LastWriteTime
            'Iterations'      = 0
            'BestWinRate'     = 0.5175
            'CurrentWinRate'  = 0
            'IsLogging'       = $true
        }

        foreach ($line in $lines) {
            if ($line -match "\[Iter\s+(\d+)\]") {
                $stats['Iterations'] = [int]$matches[1]
            }
            if ($line -match "(\d+\.?\d*)%\s+\[BEST") {
                $stats['BestWinRate'] = [double]$matches[1]
            }
            if ($line -match "(\d+\.?\d*)%\s+\[") {
                $stats['CurrentWinRate'] = [double]$matches[1]
            }
        }

        return $stats
    }
    catch {
        return @{'IsLogging' = $false}
    }
}

function Test-ProcessAlive {
    """
    Check if the process is actually producing output.
    Returns $true if new log lines have been written recently.
    """
    if (-not (Test-Path $LogFile)) {
        return $false
    }

    try {
        $lastWrite = (Get-Item $LogFile).LastWriteTime
        $secondsAgo = ((Get-Date) - $lastWrite).TotalSeconds

        # If log updated within last 60 seconds, process is alive
        return ($secondsAgo -lt 60)
    }
    catch {
        return $false
    }
}

# ============================================================================
# MAIN MONITORING LOOP
# ============================================================================

Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host "ECS CALIBRATION LIVE MONITOR v2 (Dynamic Process Detection)" -ForegroundColor Cyan
Write-Host "=====================================================================" -ForegroundColor Cyan
Write-Host ""

$processFound = $false
$process = $null
$startTime = $null

while ($true) {
    # Detect/Find calibration process
    if (-not $processFound) {
        $process = Find-CalibrationProcess

        if ($process) {
            $processFound = $true
            $startTime = $process.StartTime
            Write-Host "✅ Calibration process detected (PID: $($process.Id), Started: $($startTime.ToString('HH:mm:ss')))" -ForegroundColor Green
            Write-Host ""
        }
        else {
            Write-Host "⏳ Waiting for calibration process to start..." -ForegroundColor Yellow
            Start-Sleep -Seconds 5
            continue
        }
    }

    # Clear screen for update
    Clear-Host
    Write-Host "ECS CALIBRATION LIVE MONITOR - v2 (Dynamic)" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""

    # Check if process still exists
    $currentProcess = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
    if (-not $currentProcess) {
        Write-Host "⚠️ PROCESS ENDED (PID $($process.Id) no longer running)" -ForegroundColor Yellow
        Write-Host "Check log file for results: $LogFile" -ForegroundColor Yellow
        break
    }

    # Get calibration statistics
    $stats = Get-CalibrationStats
    $isAlive = Test-ProcessAlive

    if ($null -eq $stats) {
        Write-Host "🔴 STATUS: INITIALIZING" -ForegroundColor Yellow
        Write-Host "Waiting for log file... ($LogFile)" -ForegroundColor Gray
        Write-Host ""
    }
    else {
        # Display process info
        Write-Host "🟢 STATUS: RUNNING" -ForegroundColor Green
        Write-Host "  Process ID: $($process.Id)" -ForegroundColor Gray
        Write-Host "  Status: $(if ($isAlive) { 'Producing output' } else { '⚠️ No recent updates' })" -ForegroundColor Gray
        Write-Host ""

        # Display progress
        Write-Host "PROGRESS:" -ForegroundColor Cyan
        Write-Host "  Iterations: $($stats['Iterations']) / $MaxIterations"
        Write-Host "  Best Win Rate: $($stats['BestWinRate'])%"
        Write-Host "  Current Win Rate: $($stats['CurrentWinRate'])%"
        Write-Host ""

        # Display timing
        $elapsedSeconds = ((Get-Date) - $startTime).TotalSeconds
        $elapsedHours = $elapsedSeconds / 3600
        $elapsedFormatted = "{0:N2}" -f $elapsedHours
        $lastUpdateAge = ((Get-Date) - $stats['LastUpdate']).TotalSeconds

        Write-Host "TIMING:" -ForegroundColor Cyan
        Write-Host "  Elapsed: $elapsedFormatted hours"
        Write-Host "  Started: $($startTime.ToString('HH:mm:ss'))"
        Write-Host "  Last Update: $($stats['LastUpdate'].ToString('HH:mm:ss')) ($([math]::Round($lastUpdateAge, 0))s ago)"
        Write-Host ""

        # Calculate ETA if we have iterations
        if ($stats['Iterations'] -gt 0 -and $elapsedSeconds -gt 60) {
            $iterPerHour = ($stats['Iterations'] / $elapsedHours)
            $remainingIters = $MaxIterations - $stats['Iterations']
            $remainingHours = $remainingIters / $iterPerHour
            $etaTime = (Get-Date).AddHours($remainingHours)

            Write-Host "SPEED & ETA:" -ForegroundColor Cyan
            Write-Host "  Rate: $([math]::Round($iterPerHour, 1)) iterations/hour"
            Write-Host "  ETA: $($etaTime.ToString('MMM dd HH:mm'))"
            Write-Host "  Remaining: $([math]::Round($remainingHours, 1)) hours"
            Write-Host ""
        }

        # Alert if process not producing output
        if (-not $isAlive) {
            Write-Host "⚠️ WARNING: No updates for >60 seconds" -ForegroundColor Yellow
            Write-Host "Process may be hung. Check system resources." -ForegroundColor Yellow
            Write-Host ""
        }
    }

    # Footer
    Write-Host "COMMANDS:" -ForegroundColor Gray
    Write-Host "  Ctrl+C to stop monitoring (calibration continues in background)" -ForegroundColor Gray
    Write-Host "  Refresh interval: $RefreshSeconds seconds" -ForegroundColor Gray
    Write-Host ""

    Start-Sleep -Seconds $RefreshSeconds
}

Write-Host ""
Write-Host "Monitor stopped." -ForegroundColor Yellow
