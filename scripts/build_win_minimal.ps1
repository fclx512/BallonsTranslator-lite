<#
.SYNOPSIS
    Build the Windows bootstrap package for BallonsTranslator-lite.

.DESCRIPTION
    Produces a small ZIP (source + bare embedded Python + pip + uv.exe) that
    installs the real dependencies on first launch.  This follows upstream
    BallonsTranslator's release model: shipping a ~30 MB bootstrap instead of
    the ~1.7 GB pre-installed environment keeps a release upload cheap, while
    the pre-built environment stays available on the file host as a fallback
    for users without a usable network.

    What gets installed at runtime instead of being packed here:
      - core dependencies   -> utils/core_requirements.py::ensure_core_requirements
                               (launch.py, before config init)
      - model backends      -> modules/base.py::ensure_dependencies +
                               ui/module_manager.py::_ensure_module_deps
      - model weights       -> ui/model_downloads.py (background task)

    Source files come from `git ls-files`, so anything gitignored
    (config/config.json with API keys, ballontrans_pylibs_win, data/models
    weights, logs, ...) is excluded by construction — see the safety check
    near the end.

    Run order for a release: commit -> scripts/generate_manifest.py ->
    tag -> this script (or CI on the tag) -> upload the ZIP to the file host.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1 -KeepBuildDir
#>
param(
    # Must stay a version with an official "embeddable" build on python.org.
    [string]$PythonVersion = "3.12.4",

    # uv ships a single static executable; pin a version here when the
    # "latest" redirect is unreachable.
    [string]$UvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip",

    # Output ZIP path. Defaults to "<repo root>\BallonsTranslator-lite_win_min.zip".
    [string]$Output = "",

    # Keep build_temp/ around for inspection (it is removed on success by default).
    [switch]$KeepBuildDir
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DestName = "BallonsTranslator-lite"
$BuildDir = Join-Path $RepoRoot "build_temp"
$DestDir = Join-Path $BuildDir $DestName
$PyLibsDir = Join-Path $DestDir "ballontrans_pylibs_win"
if (-not $Output) {
    $Output = Join-Path $RepoRoot "${DestName}_win_min.zip"
}

Write-Host "=== BallonsTranslator-lite bootstrap build ==="
Write-Host "Repo root:      $RepoRoot"
Write-Host "Python version: $PythonVersion"
Write-Host "Output:         $Output"
Write-Host ""

# ---------------------------------------------------------------------------
# 0. Clean previous outputs
# ---------------------------------------------------------------------------
if (Test-Path $BuildDir) {
    Write-Host "[1/7] Removing previous build directory..."
    Remove-Item -Recurse -Force $BuildDir
}
if (Test-Path $Output) {
    Remove-Item -Force $Output
}
New-Item -ItemType Directory -Force -Path $PyLibsDir | Out-Null

# ---------------------------------------------------------------------------
# 1. Source files (tracked files only)
# ---------------------------------------------------------------------------
Write-Host "[2/7] Copying tracked source files (git ls-files)..."

# The list goes through a file, not through PowerShell's pipeline: this repo has
# Chinese file names, and reading git's UTF-8 output through the console turns
# them into mojibake — the paths then don't exist on disk and get skipped
# silently (which is exactly how 29 docs went missing from the first build).
# core.quotepath=false keeps git from octal-escaping non-ASCII names.
$ListFile = Join-Path $BuildDir "filelist.txt"
& cmd /c "git -C `"$RepoRoot`" -c core.quotepath=false ls-files > `"$ListFile`""
if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed — this script must run inside the git working tree."
}
$Files = Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_ -ne "" }
if (-not $Files) {
    throw "git ls-files returned no files."
}

$Copied = 0
$Missing = @()
foreach ($rel in $Files) {
    $src = Join-Path $RepoRoot $rel
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
        $Missing += $rel
        continue
    }
    $dst = Join-Path $DestDir $rel
    $dstParent = Split-Path -Parent $dst
    if (-not (Test-Path $dstParent)) {
        New-Item -ItemType Directory -Force -Path $dstParent | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $Copied++
}
Write-Host "      copied $Copied of $($Files.Count) tracked file(s)"
if ($Missing.Count -gt 0) {
    # A tracked file that is absent means the working tree is mid-operation
    # (deleted but not staged, unmerged, ...). Shipping that silently is worse
    # than stopping here.
    $Missing | ForEach-Object { Write-Host "      MISSING: $_" }
    throw "$($Missing.Count) tracked file(s) are missing on disk — refusing to build a partial package."
}

# ---------------------------------------------------------------------------
# 2. Embedded Python
# ---------------------------------------------------------------------------
$PyZipName = "python-$PythonVersion-embed-amd64.zip"
$PyZipPath = Join-Path $BuildDir $PyZipName
Write-Host "[3/7] Downloading Python $PythonVersion (embeddable)..."

if (Test-Path $PyZipPath) {
    Remove-Item -Force $PyZipPath
}
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/$PyZipName" -OutFile $PyZipPath -UseBasicParsing
Expand-Archive -Path $PyZipPath -DestinationPath $PyLibsDir -Force
Remove-Item -Force $PyZipPath

$PyExe = Join-Path $PyLibsDir "python.exe"
if (-not (Test-Path $PyExe)) {
    throw "python.exe not found under $PyLibsDir"
}

# ---------------------------------------------------------------------------
# 3. Enable site-packages in the ._pth file
# ---------------------------------------------------------------------------
$Parts = $PythonVersion.Split(".")
$MajorMinor = $Parts[0] + $Parts[1]
$PthFile = Join-Path $PyLibsDir "python$MajorMinor._pth"
if (-not (Test-Path $PthFile)) {
    throw "Could not find $PthFile"
}

Write-Host "[4/7] Enabling site-packages in python$MajorMinor._pth..."
$PthContent = Get-Content -Path $PthFile
$PthUpdated = @()
foreach ($line in $PthContent) {
    if ($line.Trim() -eq "#import site") {
        $PthUpdated += "import site"
    } else {
        $PthUpdated += $line
    }
}
$PthUpdated | Set-Content -Path $PthFile -Encoding ascii

# ---------------------------------------------------------------------------
# 4. pip
# ---------------------------------------------------------------------------
Write-Host "[5/7] Installing pip into the embedded Python..."
$GetPipPath = Join-Path $BuildDir "get-pip.py"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPipPath -UseBasicParsing
& $PyExe $GetPipPath --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    throw "get-pip.py failed with exit code $LASTEXITCODE"
}
Remove-Item -Force $GetPipPath

& $PyExe -m pip --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "pip is not usable after installation."
}

# ---------------------------------------------------------------------------
# 5. uv (so first-launch installs are fast; launch.py finds it next to python.exe)
# ---------------------------------------------------------------------------
Write-Host "[6/7] Fetching uv.exe..."
$UvZipPath = Join-Path $BuildDir "uv.zip"
$UvTempDir = Join-Path $BuildDir "uv_temp"
Invoke-WebRequest -Uri $UvUrl -OutFile $UvZipPath -UseBasicParsing
Expand-Archive -Path $UvZipPath -DestinationPath $UvTempDir -Force
$UvExe = Get-ChildItem -Path $UvTempDir -Filter "uv.exe" -Recurse | Select-Object -First 1
if (-not $UvExe) {
    throw "uv.exe not found in the downloaded archive."
}
Copy-Item -LiteralPath $UvExe.FullName -Destination (Join-Path $PyLibsDir "uv.exe") -Force
Remove-Item -Force $UvZipPath
Remove-Item -Recurse -Force $UvTempDir

# ---------------------------------------------------------------------------
# 6. Safety checks
# ---------------------------------------------------------------------------
Write-Host "[7/7] Verifying the staged package..."

# The one check worth hard-failing on: config.json holds API keys and must
# never ship (this is why the old build_portable.py was deleted).
$Leak = Join-Path $DestDir "config\config.json"
if (Test-Path $Leak) {
    throw "Refusing to package: config/config.json (API keys) would be shipped."
}
foreach ($OutOfScope in @("ballontrans_pylibs_win\Lib\site-packages\torch", "logs")) {
    # ballontrans_pylibs_win itself is expected (bare Python); a site-packages
    # tree means someone ran an install into the staging dir by mistake.
    if (Test-Path (Join-Path $DestDir $OutOfScope)) {
        throw "Refusing to package: unexpected $OutOfScope in the staging directory."
    }
}
if (-not (Test-Path (Join-Path $DestDir "launch.py"))) {
    throw "launch.py is missing from the staged package."
}
# Every tracked file must have landed in the staging directory. Cheap to check
# here, and it is the check that would have caught the mojibake path bug.
$StagedCount = (Get-ChildItem -LiteralPath $DestDir -File -Recurse -Force |
    Where-Object { $_.FullName -notmatch '[\\/]ballontrans_pylibs_win[\\/]' }).Count
if ($StagedCount -ne $Files.Count) {
    throw "Staged $StagedCount source file(s) but the repository tracks $($Files.Count) — package would be incomplete."
}

# ---------------------------------------------------------------------------
# 7. Zip (portable entry names, no __pycache__)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Creating $Output ..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
$ArchiveRoot = (Resolve-Path $DestDir).Path
$Archive = [System.IO.Compression.ZipFile]::Open($Output, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $ArchiveRoot -File -Recurse -Force |
        Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
        ForEach-Object {
            $RelativePath = $_.FullName.Substring($ArchiveRoot.Length + 1).Replace('\', '/')
            $EntryName = "$DestName/$RelativePath"
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $Archive, $_.FullName, $EntryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
} finally {
    $Archive.Dispose()
}

$SizeMb = [math]::Round((Get-Item $Output).Length / 1MB, 1)
if (-not $KeepBuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}

Write-Host ""
Write-Host "=== Build completed ==="
Write-Host "Output: $Output ($SizeMb MB)"
Write-Host ""
Write-Host "This package installs its dependencies on first launch:"
Write-Host "  core packages (~520 MB) via the pip/uv index configured in"
Write-Host "  config.json -> mirror.pip_index_url (auto-filled for mainland China)"
Write-Host "  model backends and weights when a module is picked in the UI"
Write-Host ""
Write-Host "Reminder: run scripts/generate_manifest.py BEFORE tagging, so the"
Write-Host "packaged manifest.json matches the released source."
