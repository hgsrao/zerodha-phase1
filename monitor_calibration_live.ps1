#!/usr/bin/env powershell
# ECS CALIBRATION LIVE MONITOR
# Tracks STAGE2 calibration progress

param(
    [int]$RefreshSeconds = 30,
    [int]$MaxIterations = 1028
)

Write-Host "CALIBRATION LIVE MONITOR" -ForegroundColor Cyan
Write-Host "=========================" -ForegroundColor Cyan
Write-Host ""

$startTime = Get-Date
$calibrationFile = "calibration_errors.txt"

function Get-CalibrationStats {
    if (-not (Test-Path $calibrationFile)) {
        return $null
    }

    try {
        $content = Get-Content $calibrationFile -Tail 100 -ErrorAction SilentlyContinue
        $lines = @($content) -split "`n"

        $stats = @{
            'TotalLines' = $lines.Count
            'LastUpdate' = (Get-Item $calibrationFile).LastWriteTime
            'IsRunning' = $true
            'Iterations' = 0
            'BestWinRate' = 0.5175
            'CurrentWinRate' = 0
        }

        foreach ($line in $lines) {
            if ($line -match "\[Iter (\d+)\]") {
                $stats['Iterations'] = [int]$matches[1]
            }
            if ($line -match "(\d+\.?\d*)% \[BEST") {
                $stats['BestWinRate'] = [double]$matches[1]
            }
            if ($line -match "(\d+\.?\d*)% \[") {
                $stats['CurrentWinRate'] = [double]$matches[1]
            }
        }

        return $stats
    }
    catch {
        return $null
    }
}

$iteration = 0

while ($true) {
    Clear-Host
    Write-Host "CALIBRATION LIVE MONITOR" -ForegroundColor Cyan
    Write-Host "=========================" -ForegroundColor Cyan
    Write-Host ""

    $stats = Get-CalibrationStats

    if ($null -eq $stats) {
        Write-Host "Waiting for calibration output..." -ForegroundColor Yellow
    }
    else {
        $elapsedSeconds = ((Get-Date) - $startTime).TotalSeconds
        $elapsedHours = $elapsedSeconds / 3600
        $elapsedFormatted = "{0:N2}" -f $elapsedHours

        Write-Host "PROGRESS:" -ForegroundColor Green
        Write-Host "  Iterations: $($stats['Iterations']) / $MaxIterations"
        Write-Host "  Best Win Rate: $($stats['BestWinRate'])%"
        Write-Host "  Current Win Rate: $($stats['CurrentWinRate'])%"
        Write-Host ""

        Write-Host "TIME:" -ForegroundColor Green
        Write-Host "  Elapsed: $elapsedFormatted hours"
        Write-Host "  Started: $($startTime.ToString('HH:mm:ss'))"
        Write-Host "  Last Update: $($stats['LastUpdate'].ToString('HH:mm:ss'))"
        Write-Host ""

        if ($stats['Iterations'] -gt 0 -and $elapsedSeconds -gt 60) {
            $iterPerHour = ($stats['Iterations'] / $elapsedHours)
            $remainingIters = $MaxIterations - $stats['Iterations']
            $remainingHours = $remainingIters / $iterPerHour
            $etaTime = (Get-Date).AddHours($remainingHours)

            Write-Host "SPEED:" -ForegroundColor Green
            Write-Host "  Rate: $([math]::Round($iterPerHour, 1)) iterations/hour"
            Write-Host "  ETA: $($etaTime.ToString('MMM dd HH:mm'))"
            Write-Host "  Remaining: $([math]::Round($remainingHours, 1)) hours"
            Write-Host ""
        }

        Write-Host "STATUS: RUNNING" -ForegroundColor Green
        Write-Host "  PID: 22860"
        Write-Host "  Process: python STAGE2_CALIBRATION_33PARAMS_24HOURS.py"
    }

    Write-Host ""
    Write-Host "NOTES:" -ForegroundColor Cyan
    Write-Host "  - Refreshing every $RefreshSeconds seconds"
    Write-Host "  - Press Ctrl+C to stop monitoring"
    Write-Host "  - Calibration continues in background"
    Write-Host ""

    Start-Sleep -Seconds $RefreshSeconds
}
