#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

# Automatically targets the folder where this .ps1 file lives
Set-Location $PSScriptRoot
Write-Host "Working directory set to: $PSScriptRoot" -ForegroundColor Cyan

$pythonTargetVersion = "3.12.8"
$blenderTargetVersion = "4.5.5"
$ffmpegTargetVersion = "7.1"

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
    $blenderRoots = @(
        "${env:ProgramFiles}\Blender Foundation",
        "${env:ProgramFiles(x86)}\Blender Foundation"
    )
    foreach ($root in $blenderRoots) {
        if (-not (Test-Path $root)) { continue }
        $exe = Get-ChildItem -Path $root -Recurse -Filter "blender.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($exe -and ($env:Path -notlike "*$($exe.DirectoryName)*")) {
            $env:Path = "$($exe.DirectoryName);$env:Path"
        }
    }

    $vfoxHome = "$HOME\.vfox"
    if ((Test-Path $vfoxHome) -and ($env:Path -notlike "*$vfoxHome*")) {
        $env:Path = "$vfoxHome;$env:Path"
    }
}

function Show-HostHardwareInventory {
    Write-Section "Host hardware inventory (Windows)"

    try {
        $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
        $cpuName = if ($cpu) { $cpu.Name.Trim() } else { "Unknown" }
        $cpuVendor = if ($cpu) { $cpu.Manufacturer } else { "Unknown" }
        $logical = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
        Write-Host "  CPU name : $cpuName" -ForegroundColor White
        Write-Host "  CPU vendor : $cpuVendor" -ForegroundColor Gray
        Write-Host "  Threads  : $logical" -ForegroundColor Gray
        if ($cpuName -match '(?i)intel') {
            Write-Host "  Class    : Intel CPU (detected)" -ForegroundColor Green
        } else {
            Write-Host "  Class    : Non-Intel CPU (still used for orchestration / software encode)" -ForegroundColor Yellow
        }
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

    if (-not $adapters -or $adapters.Count -eq 0) {
        Write-Host "  (none reported by Win32_VideoController)" -ForegroundColor Yellow
    } else {
        foreach ($gpu in $adapters) {
            $name = $gpu.Name
            $ramMB = if ($gpu.AdapterRAM -and $gpu.AdapterRAM -gt 0) {
                [math]::Round($gpu.AdapterRAM / 1MB)
            } else { $null }

            $kind = "Other GPU"
            $color = "Gray"
            if ($name -match '(?i)nvidia|geforce|quadro|rtx |gtx ') {
                $kind = "External / discrete NVIDIA GPU"
                $color = "Green"
            } elseif ($name -match '(?i)intel') {
                $kind = "Internal Intel GPU (iGPU / Arc)"
                $color = "Cyan"
            } elseif ($name -match '(?i)amd|radeon') {
                $kind = "AMD GPU"
                $color = "Yellow"
            }

            $ramText = if ($null -ne $ramMB) { " | reported VRAM ~${ramMB} MB" } else { "" }
            Write-Host "   - [$kind] $name$ramText" -ForegroundColor $color
        }
    }

    Write-Host ""
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host "  nvidia-smi:" -ForegroundColor White
        & nvidia-smi -L 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  nvidia-smi present but -L failed (driver issue?)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  nvidia-smi: not on PATH - NVIDIA CUDA/NVENC path will be unavailable" -ForegroundColor Yellow
    }
}

function Resolve-PythonExe {
    param([Parameter(Mandatory = $true)][string]$PythonCmd)
    # vfox/activate often defines a PowerShell function named "python" that can
    # drop the script path and feed "--scene" to python.exe as an interpreter flag.
    # Always resolve the real python.exe, then invoke that binary directly.
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
        [string]$Renderer = "auto"
    )

    Write-Section "Pipeline involvement (NVIDIA / Intel GPU / CPU)"
    Write-Host "  Explains what this run will use, and why unused devices are skipped." -ForegroundColor DarkGray
    Write-Host ""

    $hwScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "scripts\hw_detect.py"))
    if (-not (Test-Path -LiteralPath $hwScript)) {
        Write-Host "  Missing $hwScript - skipping involvement report." -ForegroundColor Yellow
        Write-Host "  Copy scripts\hw_detect.py from the updated animation project if this folder is a partial copy." -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path -LiteralPath $ScenePath)) {
        Write-Host "  Missing scene file: $ScenePath (report will use defaults)" -ForegroundColor Yellow
    }

    $pythonExe = Resolve-PythonExe -PythonCmd $PythonCmd
    # Use --flag=value so odd wrappers cannot steal argv positions.
    $pyArgs = @(
        $hwScript,
        "--scene=$ScenePath",
        "--renderer=$Renderer"
    )
    Write-Host "  Python : $pythonExe" -ForegroundColor DarkGray
    Write-Host "  Script : $hwScript" -ForegroundColor DarkGray
    Write-Host "  Args   : $($pyArgs -join ' ')" -ForegroundColor DarkGray
    Write-Host ""

    # Prefer env-based args so vfox shims cannot drop argv (fallback path).
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
            Write-Host "  Retrying via env (SCENE_JSON / ANIM_RENDERER) without CLI flags..." -ForegroundColor Yellow
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
        if ($null -ne $prevNative) {
            $PSNativeCommandUseErrorActionPreference = $prevNative
        }
    }
}

function Get-RendererFromArgs {
    param([string[]]$PipelineArgs)
    for ($i = 0; $i -lt $PipelineArgs.Count; $i++) {
        if ($PipelineArgs[$i] -eq "--renderer" -and ($i + 1) -lt $PipelineArgs.Count) {
            return $PipelineArgs[$i + 1]
        }
    }
    # Fall back to scene.json acceleration.renderer when present
    $scenePath = Join-Path $PSScriptRoot "data\scene.json"
    if (Test-Path $scenePath) {
        try {
            $scene = Get-Content -Raw -Path $scenePath | ConvertFrom-Json
            if ($scene.acceleration -and $scene.acceleration.renderer) {
                return [string]$scene.acceleration.renderer
            }
        } catch {
            # ignore malformed JSON here; pipeline validation will catch it
        }
    }
    return "auto"
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
    winget install --id $Id --version $Version --exact --accept-source-agreements --accept-package-agreements

    Refresh-SessionPath -WingetPackagesRoot $WingetPackagesRoot

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "$Name $Version installed but '$CommandName' is not on PATH. Open a new admin PowerShell and re-run."
    }
    Write-Host "$Name $Version is ready." -ForegroundColor Green
}

## 1. INSTALL WINGET (if not already available)
Write-Section "Bootstrap: WinGet"
if (!(Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "WinGet not found. Installing WinGet and App Installer dependencies..." -ForegroundColor Yellow

    $progressPreference = 'silentlyContinue'
    $installUrl = "https://aka.ms/getwinget"
    $installerPath = "$env:TEMP\Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"

    Invoke-WebRequest -Uri $installUrl -OutFile $installerPath
    Add-AppxPackage -Path $installerPath
    Remove-Item $installerPath -Force -ErrorAction SilentlyContinue

    Write-Host "WinGet installed successfully." -ForegroundColor Green
} else {
    Write-Host "WinGet is already installed." -ForegroundColor Green
}

## 2. REFRESH PATH SO WINGET IS USABLE IN CURRENT SESSION
$wingetPackagesRoot = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
Refresh-SessionPath -WingetPackagesRoot $wingetPackagesRoot

## 3. INSTALL VFOX (VERSION FOX) WITH EXACT PACKAGE ID
Write-Section "Bootstrap: vfox + Python"
$vfoxVersion = "0.6.2"
# Correct package ID for vfox is version-fox.vfox
$vfoxPackageId = "version-fox.vfox"

if (!(Get-Command vfox -ErrorAction SilentlyContinue)) {
    Write-Host "Installing vfox version $vfoxVersion via winget..." -ForegroundColor Yellow
    winget install --id $vfoxPackageId --version $vfoxVersion --exact --accept-source-agreements --accept-package-agreements
} else {
    Write-Host "vfox is already installed." -ForegroundColor Green
}

## 4. DYNAMICALLY RESOLVE AND APPEND VFOX PATH TO CURRENT SESSION
Refresh-SessionPath -WingetPackagesRoot $wingetPackagesRoot

# Fallback check standard paths
$fallbackPaths = @(
    "$env:LOCALAPPDATA\vfox",
    "$HOME\AppData\Local\vfox",
    "$env:ProgramFiles\vfox"
)
foreach ($p in $fallbackPaths) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) {
        $env:Path = "$p;$env:Path"
    }
}

# Final verification before session activation
if (Get-Command vfox -ErrorAction SilentlyContinue) {
    Write-Host "vfox successfully located. Activating session environment..." -ForegroundColor Green
    Invoke-Expression "$(vfox activate pwsh)"
} else {
    throw "vfox executable could not be resolved. Please verify package availability."
}

## 5. INSTALL AND APPLY PYTHON $pythonTargetVersion LOCALLY & GLOBALLY
Write-Host "Adding Python plugin to vfox..." -ForegroundColor Yellow
vfox add python

Write-Host "Installing Python version $pythonTargetVersion..." -ForegroundColor Yellow
vfox install "python@$pythonTargetVersion"

Write-Host "Activating Python $pythonTargetVersion globally and for the current session..." -ForegroundColor Yellow
vfox use -g "python@$pythonTargetVersion"

# Setup a local project version file (.vfox.toml) in the script directory
vfox use -p "python@$pythonTargetVersion"

## 6. FINAL PATH & ENVIRONMENT REFRESH FOR PYTHON
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

## 7. ENSURE BLENDER + FFMPEG AT FIXED VERSIONS (required by the animation pipeline)
Write-Section "Bootstrap: Blender + FFmpeg"

Ensure-WingetPackage -Id "BlenderFoundation.Blender" -Name "Blender" -Version $blenderTargetVersion -CommandName "blender" -WingetPackagesRoot $wingetPackagesRoot
Ensure-WingetPackage -Id "Gyan.FFmpeg" -Name "FFmpeg" -Version $ffmpegTargetVersion -CommandName "ffmpeg" -WingetPackagesRoot $wingetPackagesRoot

Write-Host "Verifying Blender and FFmpeg..." -ForegroundColor Cyan
blender --version | Select-Object -First 1
ffmpeg -version | Select-Object -First 1

## 8. Python GPU deps via pip (Windows). Nix uses python3Packages from the store instead.
Write-Section "Bootstrap: Python render deps"
Write-Host "Installing Python packages via pip: moderngl numpy pillow..." -ForegroundColor Yellow
& $pythonCmd -m pip install --upgrade pip
& $pythonCmd -m pip install moderngl numpy pillow
if ($LASTEXITCODE -ne 0) {
    Write-Host "Warning: pip install failed - use --renderer blender (winget Blender still works)" -ForegroundColor Yellow
}

## 9. HARDWARE INVENTORY + INVOLVEMENT (before render)
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
# Dot-source safety: call by name only after helpers above are defined in THIS file.
Show-PipelineInvolvement -PythonCmd $pythonCmd -ScenePath $scenePath -Renderer $rendererChoice

## 10. RUN ACCELERATED PIPELINE (runtime hardware detection - not hardcoded)
Write-Section "Render + encode"
Write-Host "==> Running accelerated pipeline (renderer arg: $rendererChoice)..." -ForegroundColor Yellow
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
Write-Host "==> Animation ready: $outputDir\animation.mp4" -ForegroundColor Green
Write-Host ""
Write-Host "Legend:" -ForegroundColor DarkGray
Write-Host "  NVIDIA  = discrete / external GPU (CUDA / OptiX / NVENC when selected)" -ForegroundColor DarkGray
Write-Host "  Intel GPU = internal iGPU / Arc (Quick Sync encode when selected)" -ForegroundColor DarkGray
Write-Host "  CPU     = always involved for orchestration; also encodes if libx264 is chosen" -ForegroundColor DarkGray
Get-ChildItem $outputDir | Format-Table Name, Length, LastWriteTime
