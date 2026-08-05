#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

# Automatically targets the folder where this .ps1 file lives
Set-Location $PSScriptRoot
Write-Host "Working directory set to: $PSScriptRoot" -ForegroundColor Cyan

$pythonTargetVersion = "3.12.8"
$blenderTargetVersion = "4.5.5"
$ffmpegTargetVersion = "7.1"

## 1. INSTALL WINGET (if not already available)
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
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

## 3. INSTALL VFOX (VERSION FOX) WITH EXACT PACKAGE ID
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
# Refresh machine/user paths first
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

# Deep search inside WinGet Packages directory where store apps/winget place binaries dynamically
$wingetPackagesRoot = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages"
if (Test-Path $wingetPackagesRoot) {
    $vfoxFolder = Get-ChildItem -Path $wingetPackagesRoot -Recurse -Filter "vfox.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($vfoxFolder) {
        $vfoxBinDir = $vfoxFolder.DirectoryName
        $env:Path = "$vfoxBinDir;$env:Path"
        Write-Host "Found vfox binary at: $vfoxBinDir" -ForegroundColor Green
    }
}

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
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

$vfoxHomeBin = "$HOME\.vfox"
if (Test-Path $vfoxHomeBin) {
    $env:Path = "$HOME\.vfox;$env:Path"
}

# Re-activate so python/python3 from the pinned vfox version are on PATH
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
function Refresh-SessionPath {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

    if (Test-Path $wingetPackagesRoot) {
        foreach ($exeName in @("blender.exe", "ffmpeg.exe")) {
            $exe = Get-ChildItem -Path $wingetPackagesRoot -Recurse -Filter $exeName -ErrorAction SilentlyContinue |
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
}

function Ensure-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Version,
        [Parameter(Mandatory = $true)][string]$CommandName
    )

    Write-Host "Installing $Name version $Version via winget ($Id)..." -ForegroundColor Yellow
    winget install --id $Id --version $Version --exact --accept-source-agreements --accept-package-agreements

    Refresh-SessionPath

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "$Name $Version installed but '$CommandName' is not on PATH. Open a new admin PowerShell and re-run."
    }
    Write-Host "$Name $Version is ready." -ForegroundColor Green
}

Ensure-WingetPackage -Id "BlenderFoundation.Blender" -Name "Blender" -Version $blenderTargetVersion -CommandName "blender"
Ensure-WingetPackage -Id "Gyan.FFmpeg" -Name "FFmpeg" -Version $ffmpegTargetVersion -CommandName "ffmpeg"

Write-Host "Verifying Blender and FFmpeg..." -ForegroundColor Cyan
blender --version | Select-Object -First 1
ffmpeg -version | Select-Object -First 1

## 8. RUN ANIMATION PIPELINE IN THE SCRIPT'S DIRECTORY
$dataDir = Join-Path $PSScriptRoot "data"
$outputDir = Join-Path $PSScriptRoot "output"
$framesDir = Join-Path $outputDir "frames"

New-Item -ItemType Directory -Force -Path $framesDir | Out-Null
$env:PROJECT_ROOT = $PSScriptRoot
$env:DATA_DIR = $dataDir
$env:OUTPUT_DIR = $outputDir

Write-Host "==> Validating structured data..." -ForegroundColor Yellow
& $pythonCmd (Join-Path $PSScriptRoot "scripts\validate_data.py")
if ($LASTEXITCODE -ne 0) { throw "Data validation failed." }

Write-Host "==> Rendering frames with Blender (headless)..." -ForegroundColor Yellow
& blender --background --python (Join-Path $PSScriptRoot "scripts\render_animation.py") -- `
    --data-dir $dataDir `
    --output-dir $outputDir
if ($LASTEXITCODE -ne 0) { throw "Blender render failed." }

Write-Host "==> Encoding video with FFmpeg..." -ForegroundColor Yellow
& $pythonCmd (Join-Path $PSScriptRoot "scripts\encode_video.py") `
    --frames-dir $framesDir `
    --output (Join-Path $outputDir "animation.mp4") `
    --scene (Join-Path $dataDir "scene.json")
if ($LASTEXITCODE -ne 0) { throw "FFmpeg encode failed." }

Write-Host "==> Animation ready: $outputDir\animation.mp4" -ForegroundColor Green
Get-ChildItem $outputDir | Format-Table Name, Length, LastWriteTime
