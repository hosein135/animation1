#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

# Bootstrap + hardware-aware pelican bicycle animation pipeline.
# Patterns adapted from ../windows_search/setup.ps1:
#   live HostInventory, DXGI LUIDs, virtual-adapter skip, vfox wrappers,
#   permanent Machine PATH for SDKs, curl download progress.

Set-Location $PSScriptRoot
Write-Host "Working directory set to: $PSScriptRoot" -ForegroundColor Cyan

$pythonTargetVersion = "3.12.8"
$blenderTargetVersion = "4.5.5"
$ffmpegTargetVersion = "7.1"

# Shared with Show-PipelineInvolvement — computed from live detection, never hardcoded.
$script:HostInventory = @{
    CpuName = "Unknown"; CpuVendor = "Unknown"; CpuClass = "Other"
    Cores = 0; Threads = 1; MemoryGB = 0
    Adapters = @(); HardwareGpuCount = 0; Luids = @(); NvidiaSmi = $false
}

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host ("=" * 64) -ForegroundColor DarkCyan
    Write-Host " $Title" -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor DarkCyan
}

function Refresh-SessionPath {
    param([string]$WingetPackagesRoot)

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")

    if ($WingetPackagesRoot -and (Test-Path $WingetPackagesRoot)) {
        foreach ($exeName in @("vfox.exe", "blender.exe", "ffmpeg.exe")) {
            $exe = Get-ChildItem -Path $WingetPackagesRoot -Recurse -Filter $exeName -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($exe -and ($env:Path -notlike "*$($exe.DirectoryName)*")) {
                $env:Path = "$($exe.DirectoryName);$env:Path"
            }
        }
    }

    # Blender MSI installs under Program Files\Blender Foundation\Blender X.Y\
    foreach ($root in @(
        "${env:ProgramFiles}\Blender Foundation",
        "${env:ProgramFiles(x86)}\Blender Foundation"
    )) {
        if (-not (Test-Path $root)) { continue }
        $exe = Get-ChildItem -Path $root -Recurse -Filter "blender.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($exe -and ($env:Path -notlike "*$($exe.DirectoryName)*")) {
            $env:Path = "$($exe.DirectoryName);$env:Path"
        }
    }

    # vfox < 1.0 → ~/.vfox/sdks ; vfox >= 1.0 → ~/.version-fox/sdks
    foreach ($sdkRoot in @("$HOME\.vfox\sdks", "$HOME\.version-fox\sdks")) {
        if (-not (Test-Path $sdkRoot)) { continue }
        $exe = Get-ChildItem -Path $sdkRoot -Recurse -Filter "python.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($exe -and ($env:Path -notlike "*$($exe.DirectoryName)*")) {
            $env:Path = "$($exe.DirectoryName);$env:Path"
        }
    }

    foreach ($vfoxHome in @("$HOME\.vfox", "$HOME\.version-fox")) {
        if ((Test-Path $vfoxHome) -and ($env:Path -notlike "*$vfoxHome*")) {
            $env:Path = "$vfoxHome;$env:Path"
        }
    }
}

function Add-VfoxSdkToMachinePath {
    param([Parameter(Mandatory = $true)][string]$ExeName)

    $cmd = Get-Command $ExeName -ErrorAction SilentlyContinue
    $binDir = $null
    if ($cmd -and $cmd.Source) {
        $binDir = Split-Path $cmd.Source -Parent
    }
    if (-not $binDir) {
        foreach ($root in @("$HOME\.vfox\sdks", "$HOME\.version-fox\sdks")) {
            if (Test-Path $root) {
                $exe = Get-ChildItem -Path $root -Recurse -Filter $ExeName -ErrorAction SilentlyContinue |
                    Select-Object -First 1
                if ($exe) { $binDir = $exe.DirectoryName; break }
            }
        }
    }
    if (-not $binDir) {
        Write-Host "  WARNING: $ExeName not found on PATH or in vfox SDK directories." -ForegroundColor Yellow
        return $false
    }

    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    if ($machinePath -like "*$binDir*") {
        if ($env:Path -notlike "*$binDir*") { $env:Path = "$binDir;$env:Path" }
        return $true
    }

    [System.Environment]::SetEnvironmentVariable("Path", "$binDir;$machinePath", "Machine")
    if ($env:Path -notlike "*$binDir*") { $env:Path = "$binDir;$env:Path" }
    Write-Host "  Added $binDir to system PATH (permanent)" -ForegroundColor Green
    return $true
}

function Invoke-Vfox {
    <# Wrapper: vfox stderr progress must not throw under $ErrorActionPreference=Stop. #>
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$VfoxArgs)
    $prevEAP = $ErrorActionPreference
    $prevNative = $null
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $prevNative = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    $ErrorActionPreference = 'Continue'
    $exe = (Get-Command vfox -ErrorAction SilentlyContinue).Source
    if (-not $exe) { $exe = 'vfox' }
    & $exe @VfoxArgs
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($null -ne $prevNative) { $PSNativeCommandUseErrorActionPreference = $prevNative }
    return $code
}

function Install-VfoxSdk {
    param(
        [Parameter(Mandatory = $true)][string]$Plugin,
        [Parameter(Mandatory = $true)][string]$Version
    )
    Write-Host "  Downloading and installing $Plugin@$Version via vfox..." -ForegroundColor Cyan
    Write-Host "  (vfox shows its own progress bar)" -ForegroundColor DarkGray
    $exitCode = Invoke-Vfox install "${Plugin}@${Version}"
    if ($exitCode -ne 0) {
        $list = Invoke-Vfox list $Plugin
        if ($list -match [regex]::Escape($Version)) {
            Write-Host "  $Plugin@$Version is already installed." -ForegroundColor Green
            return $true
        }
        Write-Host "  vfox install $Plugin@$Version failed (exit $exitCode)." -ForegroundColor Yellow
        return $false
    }
    Write-Host "  $Plugin@$Version installed." -ForegroundColor Green
    return $true
}

function Download-File {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$Label = "Downloading"
    )
    Write-Host "  $Label" -ForegroundColor Cyan
    Write-Host "  URL: $Url" -ForegroundColor DarkGray
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & curl.exe -L -# -o "$Destination" "$Url"
        $curlExit = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($curlExit -eq 0 -and (Test-Path $Destination)) {
            $sizeMB = [math]::Round((Get-Item $Destination).Length / 1MB, 1)
            Write-Host "  Done ($sizeMB MB)" -ForegroundColor Green
            return $true
        }
    }
    $prevProgress = $ProgressPreference
    $ProgressPreference = 'Continue'
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Destination
        $sizeMB = [math]::Round((Get-Item $Destination).Length / 1MB, 1)
        Write-Host "  Done ($sizeMB MB)" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "  Download failed: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    } finally {
        $ProgressPreference = $prevProgress
    }
}

function Get-AdapterLuids {
    <# DXGI adapter LUIDs from GPU Adapter Memory perf counters (multi-GPU pin hints). #>
    $luids = @()
    try {
        $counter = Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction Stop
        foreach ($s in $counter.CounterSamples) {
            if ($s.InstanceName -match 'luid_0x([0-9a-f]{8})_0x([0-9a-f]{8})') {
                $hex = "0x$($Matches[1])_0x$($Matches[2])".ToUpper().Replace("0X", "0x")
                if ($luids -notcontains $hex) { $luids += $hex }
            }
        }
    } catch {
        # Perf counters missing (Server Core / stripped VMs).
    }
    return $luids
}

function Show-HostHardwareInventory {
    Write-Section "Host hardware inventory (Windows)"

    try {
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $cs = Get-CimInstance Win32_ComputerSystem
        $cpuName = if ($cpu) { $cpu.Name.Trim() } else { "Unknown" }
        $cpuVendor = if ($cpu) { $cpu.Manufacturer } else { "Unknown" }
        $cores = 0
        foreach ($p in @(Get-CimInstance Win32_Processor)) { $cores += [int]$p.NumberOfCores }
        $logical = [int]$cs.NumberOfLogicalProcessors
        if ($logical -lt 1) { $logical = [Environment]::ProcessorCount }
        $memGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)

        $cpuClass = "Other"
        $classColor = "Yellow"
        if ($cpuName -match '(?i)intel' -or $cpuVendor -match '(?i)intel') { $cpuClass = "Intel"; $classColor = "Green" }
        elseif ($cpuName -match '(?i)amd|ryzen|epyc' -or $cpuVendor -match '(?i)amd') { $cpuClass = "AMD"; $classColor = "Green" }
        elseif ($cpuName -match '(?i)arm|snapdragon|qualcomm') { $cpuClass = "ARM"; $classColor = "Cyan" }

        $script:HostInventory.CpuName = $cpuName
        $script:HostInventory.CpuVendor = $cpuVendor
        $script:HostInventory.CpuClass = $cpuClass
        $script:HostInventory.Cores = $cores
        $script:HostInventory.Threads = $logical
        $script:HostInventory.MemoryGB = $memGB

        Write-Host "  CPU name   : $cpuName" -ForegroundColor White
        Write-Host "  CPU vendor : $cpuVendor" -ForegroundColor Gray
        Write-Host "  Cores      : $cores physical / $logical logical threads" -ForegroundColor Gray
        Write-Host "  RAM        : ~$memGB GB" -ForegroundColor Gray
        Write-Host "  Class      : $cpuClass — Blender workers + FFmpeg threads scale from logical count" -ForegroundColor $classColor
    } catch {
        Write-Host "  CPU query failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "  Display adapters:" -ForegroundColor White
    $adapters = @()
    try {
        $adapters = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name })
    } catch {
        Write-Host "  Adapter query failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    $hwCount = 0
    if (-not $adapters -or $adapters.Count -eq 0) {
        Write-Host "  (none reported by Win32_VideoController)" -ForegroundColor Yellow
    } else {
        foreach ($gpu in $adapters) {
            $name = $gpu.Name
            $ramMB = if ($gpu.AdapterRAM -and $gpu.AdapterRAM -gt 0) { [math]::Round($gpu.AdapterRAM / 1MB) } else { $null }
            $kind = "Other GPU"; $color = "Gray"; $isHardware = $true
            if ($name -match '(?i)nvidia|geforce|quadro|rtx |gtx ') {
                $kind = "External / discrete NVIDIA GPU"; $color = "Green"
            } elseif ($name -match '(?i)intel') {
                $kind = "Internal Intel GPU (iGPU / Arc)"; $color = "Cyan"
            } elseif ($name -match '(?i)amd|radeon') {
                $kind = "AMD GPU"; $color = "Yellow"
            } elseif ($name -match '(?i)basic display|basic render|remote display|virtual|vmware|virtualbox|hyper-v|qxl|parallels') {
                $kind = "Basic / virtual display adapter (skipped for GPU render)"; $color = "DarkGray"; $isHardware = $false
            }
            if ($isHardware) { $hwCount++ }
            $ramText = if ($null -ne $ramMB) { " | reported VRAM ~${ramMB} MB" } else { "" }
            $drvText = if ($gpu.DriverVersion) { " | driver $($gpu.DriverVersion)" } else { "" }
            Write-Host "   - [$kind] $name$ramText$drvText" -ForegroundColor $color
        }
    }
    $script:HostInventory.Adapters = $adapters
    $script:HostInventory.HardwareGpuCount = $hwCount

    $luids = @(Get-AdapterLuids)
    $script:HostInventory.Luids = $luids
    if ($luids.Count -gt 0) {
        Write-Host "  DXGI adapter LUIDs : $($luids -join ', ')" -ForegroundColor Gray
    } else {
        Write-Host "  DXGI adapter LUIDs : unavailable (GPU perf counters missing)" -ForegroundColor Yellow
    }

    Write-Host ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host "  nvidia-smi:" -ForegroundColor White
        & nvidia-smi -L 2>$null
        if ($LASTEXITCODE -eq 0) { $script:HostInventory.NvidiaSmi = $true }
        else { Write-Host "  nvidia-smi present but -L failed (driver issue?)" -ForegroundColor Yellow }
    } else {
        Write-Host "  nvidia-smi: not on PATH — CUDA/OptiX/NVENC path may be unavailable; EEVEE / libx264 still work" -ForegroundColor Yellow
    }
}

function Resolve-PythonExe {
    param([Parameter(Mandatory = $true)][string]$PythonCmd)
    try {
        $probe = & $PythonCmd -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) {
            $exe = ($probe | Select-Object -Last 1).ToString().Trim()
            if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
        }
    } catch { }

    $cmd = Get-Command $PythonCmd -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source) -and ($cmd.Source -match '\.exe$')) {
        return $cmd.Source
    }
    return $PythonCmd
}

function Show-PipelineInvolvement {
    param(
        [Parameter(Mandatory = $true)][string]$PythonCmd,
        [Parameter(Mandatory = $true)][string]$ScenePath,
        [string]$Renderer = "blender"
    )

    Write-Section "Pipeline involvement (NVIDIA / Intel GPU / CPU)"
    Write-Host "  What this pelican render will use, from live inventory + hw_detect.py." -ForegroundColor DarkGray
    Write-Host ""

    $threads = [int]$script:HostInventory.Threads
    if ($threads -lt 1) { $threads = 1 }
    $hwGpus = [int]$script:HostInventory.HardwareGpuCount
    $adapters = @($script:HostInventory.Adapters)
    $nvidia = @($adapters | Where-Object { $_.Name -match '(?i)nvidia|geforce|quadro|rtx |gtx ' })
    $intel  = @($adapters | Where-Object { $_.Name -match '(?i)intel' })
    $amd    = @($adapters | Where-Object { $_.Name -match '(?i)amd|radeon' })

    Write-Host "  Blender Cycles / EEVEE:" -ForegroundColor White
    if ($hwGpus -eq 0) {
        Write-Host "   - no hardware GPU → Cycles CPU or EEVEE software path; encode falls to libx264" -ForegroundColor Yellow
    } else {
        if ($nvidia.Count -gt 0) {
            Write-Host "   - prefer OptiX/CUDA on $($nvidia[0].Name) (+ CPU hybrid tiles)" -ForegroundColor Green
        } elseif ($amd.Count -gt 0) {
            Write-Host "   - prefer HIP on $($amd[0].Name) when Blender supports it" -ForegroundColor Yellow
        } elseif ($intel.Count -gt 0) {
            Write-Host "   - try ONEAPI / EEVEE on $($intel[0].Name)" -ForegroundColor Cyan
        }
        Write-Host "   - parallel Blender workers sized from CPU/GPU counts (see hw_detect)" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "  Encode:" -ForegroundColor White
    if ($script:HostInventory.NvidiaSmi -or $nvidia.Count -gt 0) {
        Write-Host "   - try NVENC first when FFmpeg probe succeeds" -ForegroundColor Green
    }
    if ($intel.Count -gt 0) {
        Write-Host "   - else Intel Quick Sync (h264_qsv) when probe succeeds" -ForegroundColor Cyan
    }
    Write-Host "   - fallback: threaded libx264 on all $threads logical CPUs" -ForegroundColor Gray

    Write-Host ""
    Write-Host "  CPU ($($script:HostInventory.CpuClass), $threads threads, ~$($script:HostInventory.MemoryGB) GB RAM):" -ForegroundColor White
    Write-Host "   - orchestration, Blender BVH/hybrid, FFmpeg mux" -ForegroundColor Cyan
    Write-Host ""

    $hwScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "scripts\hw_detect.py"))
    if (-not (Test-Path -LiteralPath $hwScript)) {
        Write-Host "  Missing $hwScript - skipping Python involvement report." -ForegroundColor Yellow
        return
    }

    $pythonExe = Resolve-PythonExe -PythonCmd $PythonCmd
    $pyArgs = @($hwScript, "--scene=$ScenePath", "--renderer=$Renderer")
    Write-Host "  Python runtime report:" -ForegroundColor White
    Write-Host "  $pythonExe $($pyArgs -join ' ')" -ForegroundColor DarkGray
    Write-Host ""

    $prevScene = $env:SCENE_JSON
    $prevRenderer = $env:ANIM_RENDERER
    $env:SCENE_JSON = $ScenePath
    $env:ANIM_RENDERER = $Renderer
    $prevNative = $null
    if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
        $prevNative = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }
    try {
        & $pythonExe @pyArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Retrying via env (SCENE_JSON / ANIM_RENDERER)..." -ForegroundColor Yellow
            & $pythonExe @($hwScript)
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Involvement report failed (exit $LASTEXITCODE)." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Involvement report error: $($_.Exception.Message)" -ForegroundColor Yellow
    } finally {
        if ($null -ne $prevScene) { $env:SCENE_JSON = $prevScene } else { Remove-Item Env:SCENE_JSON -ErrorAction SilentlyContinue }
        if ($null -ne $prevRenderer) { $env:ANIM_RENDERER = $prevRenderer } else { Remove-Item Env:ANIM_RENDERER -ErrorAction SilentlyContinue }
        if ($null -ne $prevNative) { $PSNativeCommandUseErrorActionPreference = $prevNative }
    }
}

function Get-RendererFromArgs {
    param([string[]]$PipelineArgs)
    for ($i = 0; $i -lt $PipelineArgs.Count; $i++) {
        if ($PipelineArgs[$i] -eq "--renderer" -and ($i + 1) -lt $PipelineArgs.Count) {
            return $PipelineArgs[$i + 1]
        }
    }
    $scenePath = Join-Path $PSScriptRoot "data\scene.json"
    if (Test-Path $scenePath) {
        try {
            $scene = Get-Content -Raw -Path $scenePath | ConvertFrom-Json
            if ($scene.acceleration -and $scene.acceleration.renderer) {
                return [string]$scene.acceleration.renderer
            }
        } catch { }
    }
    return "blender"
}

function Ensure-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][string]$WingetPackagesRoot
    )

    Refresh-SessionPath -WingetPackagesRoot $WingetPackagesRoot

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        Write-Host "$Name is already installed; skipping download." -ForegroundColor Green
        return
    }

    Write-Host "$Name not found. Installing version $Version via winget ($Id)..." -ForegroundColor Yellow
    Write-Host "  (winget shows its own progress bar)" -ForegroundColor DarkGray
    winget install --id $Id --version $Version --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "$Name $Version unavailable; trying latest $Id..." -ForegroundColor Yellow
        winget install --id $Id --accept-source-agreements --accept-package-agreements
    }

    Refresh-SessionPath -WingetPackagesRoot $WingetPackagesRoot

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "$Name installed but '$CommandName' is not on PATH. Open a new admin PowerShell and re-run."
    }
    Write-Host "$Name is ready." -ForegroundColor Green
}

## 1. INSTALL WINGET
Write-Section "Bootstrap: WinGet"
if (!(Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "WinGet not found. Installing WinGet and App Installer dependencies..." -ForegroundColor Yellow
    $installerPath = "$env:TEMP\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
    $downloaded = Download-File -Url "https://aka.ms/getwinget" -Destination $installerPath -Label "Downloading WinGet (App Installer)"
    if (-not $downloaded) { throw "Failed to download WinGet." }
    Add-AppxPackage -Path $installerPath
    Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
    Write-Host "WinGet installed successfully." -ForegroundColor Green
} else {
    Write-Host "WinGet is already installed." -ForegroundColor Green
}

## 2. PATH REFRESH
$wingetPackagesRoot = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
Refresh-SessionPath -WingetPackagesRoot $wingetPackagesRoot

## 3. VFOX
Write-Section "Bootstrap: vfox + Python"
$vfoxVersion = "0.6.2"
$vfoxPackageId = "version-fox.vfox"

if (!(Get-Command vfox -ErrorAction SilentlyContinue)) {
    Write-Host "Installing vfox version $vfoxVersion via winget..." -ForegroundColor Yellow
    winget install --id $vfoxPackageId --version $vfoxVersion --exact --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Failed to install vfox $vfoxVersion." }
} else {
    Write-Host "vfox is already installed." -ForegroundColor Green
}

Refresh-SessionPath -WingetPackagesRoot $wingetPackagesRoot
foreach ($p in @(
    "$env:LOCALAPPDATA\vfox",
    "$HOME\AppData\Local\vfox",
    "$env:ProgramFiles\vfox"
)) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path = "$p;$env:Path" }
}

if (Get-Command vfox -ErrorAction SilentlyContinue) {
    Write-Host "vfox successfully located. Activating session environment..." -ForegroundColor Green
    Invoke-Expression "$(vfox activate pwsh)"
} else {
    throw "vfox executable could not be resolved. Please verify package availability."
}

## 4. PYTHON
Write-Host "Adding Python plugin to vfox..." -ForegroundColor Yellow
Invoke-Vfox add python | Out-Null

Write-Host "Installing Python version $pythonTargetVersion..." -ForegroundColor Yellow
Install-VfoxSdk -Plugin "python" -Version $pythonTargetVersion | Out-Null

Write-Host "Activating Python $pythonTargetVersion globally and for the current session..." -ForegroundColor Yellow
Invoke-Vfox use -g "python@$pythonTargetVersion" | Out-Null
Invoke-Vfox use -p "python@$pythonTargetVersion" | Out-Null

if (Get-Command vfox -ErrorAction SilentlyContinue) {
    Invoke-Expression "$(vfox activate pwsh)"
}
Add-VfoxSdkToMachinePath -ExeName "python.exe" | Out-Null

Refresh-SessionPath -WingetPackagesRoot $wingetPackagesRoot
if (Get-Command vfox -ErrorAction SilentlyContinue) {
    Invoke-Expression "$(vfox activate pwsh)"
}

Write-Host "Verifying Python version..." -ForegroundColor Cyan
$pythonCmd = $null
foreach ($candidate in @("python", "python3")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCmd = $candidate
        break
    }
}
if (-not $pythonCmd) {
    throw "Python executable could not be resolved after vfox install."
}
& $pythonCmd --version

## 5. BLENDER + FFMPEG
Write-Section "Bootstrap: Blender + FFmpeg"
Ensure-WingetPackage -Id "BlenderFoundation.Blender" -Name "Blender" -Version $blenderTargetVersion -CommandName "blender" -WingetPackagesRoot $wingetPackagesRoot
Ensure-WingetPackage -Id "Gyan.FFmpeg" -Name "FFmpeg" -Version $ffmpegTargetVersion -CommandName "ffmpeg" -WingetPackagesRoot $wingetPackagesRoot

Write-Host "Verifying Blender and FFmpeg..." -ForegroundColor Cyan
blender --version | Select-Object -First 1
ffmpeg -version | Select-Object -First 1

## 6. HARDWARE INVENTORY + INVOLVEMENT
$dataDir = Join-Path $PSScriptRoot "data"
$outputDir = Join-Path $PSScriptRoot "output"
$framesDir = Join-Path $outputDir "frames"
$scenePath = [System.IO.Path]::GetFullPath((Join-Path $dataDir "scene.json"))

New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
$env:PROJECT_ROOT = $PSScriptRoot
$env:DATA_DIR = $dataDir
$env:OUTPUT_DIR = $outputDir

$pipelineArgs = @()
if ($args.Count -gt 0) { $pipelineArgs = @($args) }
$rendererChoice = Get-RendererFromArgs -PipelineArgs $pipelineArgs

Show-HostHardwareInventory
Show-PipelineInvolvement -PythonCmd $pythonCmd -ScenePath $scenePath -Renderer $rendererChoice

## 7. RENDER + ENCODE
Write-Section "Render + encode (pelican coastal parade)"
Write-Host "==> Running pipeline (renderer: $rendererChoice)..." -ForegroundColor Yellow
$pythonExe = Resolve-PythonExe -PythonCmd $pythonCmd
$pipelineScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "scripts\pipeline.py"))
$prevNative = $null
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $prevNative = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
try {
    $allArgs = @($pipelineScript) + @($pipelineArgs)
    & $pythonExe @allArgs
    if ($LASTEXITCODE -ne 0) { throw "Pipeline failed (exit $LASTEXITCODE)." }
} finally {
    if ($null -ne $prevNative) {
        $PSNativeCommandUseErrorActionPreference = $prevNative
    }
}

Write-Section "Done"
$planThreads = [int]$script:HostInventory.Threads
if ($planThreads -lt 1) { $planThreads = [Environment]::ProcessorCount }
$planGpus = [int]$script:HostInventory.HardwareGpuCount
Write-Host "==> Animation ready: $outputDir\animation.mp4" -ForegroundColor Green
Write-Host "==> This machine plan: $planThreads threads, $planGpus hardware GPU adapter(s)" -ForegroundColor Green
Write-Host ""
Write-Host "Legend:" -ForegroundColor DarkGray
Write-Host "  NVIDIA  = discrete GPU (CUDA / OptiX / NVENC when selected)" -ForegroundColor DarkGray
Write-Host "  Intel GPU = iGPU / Arc (Quick Sync encode when selected)" -ForegroundColor DarkGray
Write-Host "  Virtual / Basic display = detected, skipped for GPU render" -ForegroundColor DarkGray
Write-Host "  CPU     = always involved (orchestration, BVH, libx264 fallback)" -ForegroundColor DarkGray
Get-ChildItem $outputDir | Format-Table Name, Length, LastWriteTime
