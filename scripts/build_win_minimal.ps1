<#
.SYNOPSIS
    构建 BallonsTranslator-lite 的 Windows 精简包（lite package）。

.DESCRIPTION
    产物是一个解压即用的自包含 ZIP（压缩后约 550–600 MB，实际以产物为准）：

      - `git ls-files` 跟踪的源码（含 docs/tests/scripts——**初版刻意不裁剪**，
        保持与 updater/manifest 的受管文件范围一致，否则增量更新会把砍掉的
        开发文件重新拉回来）；
      - 嵌入式 Python（版本可参数化）+ pip + **固定版本**的 uv.exe；
      - 用 staging 里的 python/uv 预装 `requirements.txt` 的基本依赖；
      - `data/libs/patchmatch_inpaint.dll` + `data/libs/opencv_world455.dll`：
        PatchMatch 是低占用、非模型的基础修复能力，精简包必须开箱可用。

    刻意不入包（也不允许混进来，脚本会硬校验）：

      - 模型后端：torch / torchvision / transformers / diffusers /
        ultralytics / onnxruntime / onnxocr；
      - numba / llvmlite（效果渲染加速，缺了会自动退回纯 NumPy）；
      - `data/models` 下的任何模型权重；
      - `config/config.json`（含 API 密钥）、`logs/`。

    模型后端与权重由 GUI 按需补全：后端在加载模块时由
    `modules/base.py::ensure_dependencies` 安装，权重走设置页
    「Models → 模型文件」（`ui/model_downloads.py` 后台下载）。
    ~1.7 GB 的预装完整包继续留在网盘，作为没有可用网络时的离线兜底。

    发版顺序：提交 → `scripts/generate_manifest.py` → 打 tag → 本脚本 →
    上传 ZIP。默认（发行门禁）要求工作区干净；`manifest.json` 与
    `pyproject.toml` 的版本必须一致——不一致直接失败，不是提醒。
    `-AllowDirtyWorkTree` 只放宽「工作区干净 + manifest 覆盖/哈希」这两项
    发行门禁供日常调试；版本一致性、原生资产、精简包成分校验不受它影响。

.PARAMETER StaticCheck
    只跑不依赖网络与产物的校验（源一致性、版本钉定、原生资产），并打印
    计划打包的成分清单，然后退出。用于无网络或本机缺 DLL 的机器上核对逻辑。

.EXAMPLE
    # 发行门禁：工作区必须干净
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1

    # 日常调试：允许脏工作区、保留中间目录
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1 -AllowDirtyWorkTree -KeepBuildDir

    # 离线预检（不下载、不产出）
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1 -StaticCheck -AllowDirtyWorkTree

    # 内网/镜像构建：把三个下载指到本地归档或镜像
    powershell -ExecutionPolicy Bypass -File scripts\build_win_minimal.ps1 -IndexUrl https://mirror.example/simple -UvUrl D:\archives\uv-0.12.19.zip
#>
param(
    # 必须是有官方 embeddable 构建的版本（python.org）。
    [string]$PythonVersion = "3.12.4",

    # 覆盖 embeddable 归档地址：本地文件路径或镜像 URL。留空按版本拼 python.org。
    [string]$PythonEmbedUrl = "",

    # 钉定的 uv 版本，**不是 latest**：同一份源码必须能重复构建出同一份产物。
    # 升级请显式改这里（必要时同步 -UvZipSha256）。禁止 URL 里出现 /latest/。
    [string]$UvVersion = "0.12.19",
    # 整体覆盖 uv 归档地址（本地文件路径或镜像 URL）；给出后 -UvVersion 只用于文案。
    [string]$UvUrl = "",

    # 两个归档的可选 SHA256 钉定（64 位 hex，不带 sha256: 前缀）。
    [string]$PythonZipSha256 = "",
    [string]$UvZipSha256 = "",

    # 装依赖时用的 pip 源（如国内镜像）。留空用官方源。
    [string]$IndexUrl = "",

    # pip 引导脚本：嵌入式 Python 不含 ensurepip，必须外部引导。
    [string]$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py",

    # 原生资产。两个 DLL 都被 `.gitignore` 忽略（`*.dll`），所以它们只能是
    # 显式构建输入，不能靠 git ls-files 带进来；缺任一文件直接失败。
    [string]$PatchMatchDll = "",
    [string]$OpencvWorldDll = "",

    # 输出 ZIP（默认 `<repo root>\BallonsTranslator-lite_win_min.zip`）。
    [string]$Output = "",

    # 调试用：放宽发行门禁（工作区干净 + manifest 覆盖/哈希）。版本一致性、
    # 精简包成分、原生资产校验仍然强制。
    [switch]$AllowDirtyWorkTree,

    # 只做离线校验并打印计划成分，不下载、不产出。
    [switch]$StaticCheck,

    # 保留 build_temp/ 供检查（默认成功后删除）。
    [switch]$KeepBuildDir
)

$ErrorActionPreference = "Stop"
$TotalSteps = 11

# ---------------------------------------------------------------------------
# 精简包的两份清单：允许出现的 site-packages，与绝不允许出现的东西
# ---------------------------------------------------------------------------

# 不允许进入精简包的重依赖（模型后端 + 加速）。与 pyproject.toml
# [project.optional-dependencies] 的 gpu / acc / onnx 三组 + onnxocr（由模块
# requires_packages 安装、不在 pyproject 里）保持同步：
# tests/test_minimal_package_contract.py 会核对这份清单没有漏项。
# 按前缀匹配（torch 同时盖住 torchvision），大小写与 -/_ 差异都归一化。
$ForbiddenPackages = @(
    "torch",
    "torchvision",
    "transformers",
    "diffusers",
    "ultralytics",
    "onnxruntime",
    "onnxruntime-gpu",
    "onnxocr",
    "numba",
    "llvmlite"
)

# data/models 下不允许出现的权重后缀。
$WeightExtensions = @(
    ".onnx", ".pt", ".pth", ".safetensors", ".ckpt", ".h5", ".tflite",
    ".pdparams", ".bin"
)

# 包内必须能找到的发行版（按 `<name>-<version>.dist-info/METADATA` 匹配，避免
# 依赖某个 wheel 的模块目录布局）。核心 import 的权威检查在 staging 上跑
# utils/core_requirements.py::check_core_imports；这份清单是 zip 结构层的复核。
$ExpectedDistInfos = @(
    "numpy", "pillow", "pillow_jxl_plugin", "opencv_python", "pyqt6",
    "pyqt6_qt6", "shapely", "pyclipper", "pydantic", "httpx", "requests",
    "pyyaml", "pywin32", "packaging", "py7zr", "openai", "spacy_pkuseg",
    "tqdm", "qtpy", "fonttools", "einops", "natsort", "networkx", "colorama",
    "termcolor", "opencc_python_reimplemented"
)

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DestName = "BallonsTranslator-lite"
$BuildDir = Join-Path $RepoRoot "build_temp"
$DestDir = Join-Path $BuildDir $DestName
$PyLibsDir = Join-Path $DestDir "ballontrans_pylibs_win"
$SitePackages = Join-Path $PyLibsDir "Lib\site-packages"
$RequirementsFile = Join-Path $RepoRoot "requirements.txt"
$PyprojectFile = Join-Path $RepoRoot "pyproject.toml"
$ManifestFile = Join-Path $RepoRoot "manifest.json"

if (-not $Output) {
    $Output = Join-Path $RepoRoot "${DestName}_win_min.zip"
}
if (-not $PatchMatchDll) {
    $PatchMatchDll = Join-Path $RepoRoot "data\libs\patchmatch_inpaint.dll"
}
if (-not $OpencvWorldDll) {
    $OpencvWorldDll = Join-Path $RepoRoot "data\libs\opencv_world455.dll"
}
if (-not $PythonEmbedUrl) {
    $PythonEmbedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
}
if (-not $UvUrl) {
    if (-not $UvVersion) {
        throw "-UvVersion 为空且未给 -UvUrl：uv 必须钉定版本（禁止 latest 下载）。"
    }
    $UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
}
# 期望随包携带的两个原生资产（名字必须与 data/libs 下的真名一致，否则
# 运行时 modules/inpaint/patch_match.py 按 'data/libs/<name>' 找不到）。
$NativeAssets = @(
    @{ Name = "patchmatch_inpaint.dll"; Path = $PatchMatchDll },
    @{ Name = "opencv_world455.dll"; Path = $OpencvWorldDll }
)

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
function Step {
    param([int]$Index, [string]$Text)
    Write-Host ""
    Write-Host "[$Index/$TotalSteps] $Text"
}

function Invoke-GitLines {
    # git 的输出先落文件再按 UTF-8 读：本仓库有中文文件名，走控制台管道会被
    # 转成乱码，路径随即在磁盘上"不存在"并被静默跳过（第一次构建就是这样
    # 丢了 29 篇文档）。core.quotepath=false 阻止 git 把非 ASCII 转义成八进制。
    param([string[]]$GitArgs)
    if (-not (Test-Path -LiteralPath $BuildDir)) {
        New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    }
    $OutFile = Join-Path $BuildDir "git_out.txt"
    $ErrFile = Join-Path $BuildDir "git_err.txt"
    if (Test-Path -LiteralPath $OutFile) { Remove-Item -Force $OutFile }
    if (Test-Path -LiteralPath $ErrFile) { Remove-Item -Force $ErrFile }

    $CmdLine = "git -C `"$RepoRoot`" -c core.quotepath=false $($GitArgs -join ' ') > `"$OutFile`" 2> `"$ErrFile`""
    & cmd /c $CmdLine
    $Code = $LASTEXITCODE

    $Text = ""
    if (Test-Path -LiteralPath $OutFile) {
        $Text = Get-Content -LiteralPath $OutFile -Encoding UTF8 -Raw
        if ($null -eq $Text) { $Text = "" }
    }
    if ($Code -ne 0) {
        $ErrText = ""
        if (Test-Path -LiteralPath $ErrFile) {
            $ErrText = Get-Content -LiteralPath $ErrFile -Encoding UTF8 -Raw
        }
        throw "git $($GitArgs -join ' ') 失败（exit $Code）：$ErrText"
    }
    if ([string]::IsNullOrWhiteSpace($Text)) { return @() }
    return @($Text -split "`r?`n" | Where-Object { $_ -ne "" })
}

function Get-RemoteFile {
    # 支持本地路径 / 镜像 URL：无网络时用 -PythonEmbedUrl / -UvUrl /
    # -GetPipUrl 指到本机归档即可，不需要联网。
    param([string]$Url, [string]$Destination, [string]$What)
    if (Test-Path -LiteralPath $Url -PathType Leaf) {
        Write-Host "      COPY $Url"
        Copy-Item -LiteralPath $Url -Destination $Destination -Force
        return
    }
    Write-Host "      GET  $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
    } catch {
        throw @"
下载 $What 失败：$Url
  $($_.Exception.Message)
  没有网络时请先准备好归档，再指到本地路径或内网镜像，例如：
    -PythonEmbedUrl D:\archives\python-$PythonVersion-embed-amd64.zip
    -UvUrl D:\archives\uv-$UvVersion.zip
    -GetPipUrl D:\archives\get-pip.py
"@
    }
}

function Assert-FileSha256 {
    param([string]$Path, [string]$Expected, [string]$What)
    if (-not $Expected) { return }
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($Actual -ne $Expected.Replace("sha256:", "")) {
        throw "$What SHA256 不匹配：期望 $Expected，实际 $Actual（$Path）"
    }
    Write-Host "      SHA256 OK ($What)"
}

function Get-ForbiddenHitsInDir {
    # 扫描一个类似 site-packages 的目录，返回命中的禁用包（目录或 dist-info）。
    param([string]$Directory)
    $Hits = @()
    if (-not (Test-Path -LiteralPath $Directory)) { return $Hits }
    $Tokens = @($ForbiddenPackages | ForEach-Object { $_.ToLower().Replace('-', '_') })
    foreach ($Entry in (Get-ChildItem -LiteralPath $Directory -Force)) {
        $Name = $Entry.Name
        if (($Name -like "*.pth") -or ($Name -like "*.py") -or ($Name -like "*.pyc")) {
            continue
        }
        if (($Name -like "*.dist-info") -or ($Name -like "*.egg-info")) {
            $Name = ($Name -split '-')[0]
        }
        $Base = $Name.ToLower().Replace('-', '_')
        foreach ($Token in $Tokens) {
            if (($Base -eq $Token) -or ($Base -like "$Token*")) {
                $Hits += $Entry.Name
                break
            }
        }
    }
    return $Hits
}

function Format-Size {
    param([double]$Bytes)
    if ($Bytes -ge 1MB) { return "{0:N1} MB" -f ($Bytes / 1MB) }
    if ($Bytes -ge 1KB) { return "{0:N0} KB" -f ($Bytes / 1KB) }
    return "{0:N0} B" -f [math]::Round($Bytes)
}

function Assert-PackageArchive {
    # 打开产物逐条核对——"看起来打包成功"不算过：python.exe/pip/uv.exe/基本依赖
    # 是否在位、两个原生 DLL 的条目与大小、以及精简包成分（模型后端/numba、权重、
    # config.json、logs 一个都不许有）都在这里兜。
    param([string]$ArchivePath)

    $SitePkgsEntryPrefix = "$DestName/ballontrans_pylibs_win/Lib/site-packages/"

    $Read = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        $Entries = @{}
        foreach ($Entry in $Read.Entries) {
            $Entries[$Entry.FullName] = $Entry
        }
    } finally {
        $Read.Dispose()
    }

    $RequiredEntries = @(
        "$DestName/launch.py",
        "$DestName/requirements.txt",
        "$DestName/pyproject.toml",
        "$DestName/manifest.json",
        "$DestName/ballontrans_pylibs_win/python.exe",
        "$DestName/ballontrans_pylibs_win/uv.exe",
        "${SitePkgsEntryPrefix}pip/__init__.py"
    )
    foreach ($RequiredEntry in $RequiredEntries) {
        if (-not $Entries.ContainsKey($RequiredEntry)) {
            throw "产物里缺少条目：$RequiredEntry"
        }
    }

    # 两个原生 DLL：条目存在 + 大小与源文件完全一致。
    foreach ($Asset in $NativeAssets) {
        $EntryName = "$DestName/data/libs/$($Asset.Name)"
        if (-not $Entries.ContainsKey($EntryName)) {
            throw "产物里缺少原生资产：$EntryName"
        }
        if ($Entries[$EntryName].Length -ne $Asset.Size) {
            throw "产物里的 $($Asset.Name) 大小不符：$($Entries[$EntryName].Length) 字节，源文件 $($Asset.Size) 字节。"
        }
    }

    # 基本依赖在位（按 dist-info 复核，避免绑死某个 wheel 的模块布局；核心
    # import 的权威检查已在 staging 上跑过 check_core_imports）。
    foreach ($DistName in $ExpectedDistInfos) {
        $Hit = $Entries.Keys | Where-Object { $_ -like "$SitePkgsEntryPrefix$DistName-*.dist-info/METADATA" }
        if (-not $Hit) {
            throw "产物 site-packages 里找不到预期的包：$DistName（requirements.txt 的基本依赖没装全）。"
        }
    }

    # 精简包成分：模型后端/numba 一个都不许有（前缀匹配，大小写与 -/_ 都归一化）。
    $ForbiddenTokens = @($ForbiddenPackages | ForEach-Object { $_.ToLower().Replace('-', '_') })
    $ForbiddenEntryHits = @()
    foreach ($Key in $Entries.Keys) {
        if (-not $Key.StartsWith($SitePkgsEntryPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $Rest = $Key.Substring($SitePkgsEntryPrefix.Length)
        $Top = ($Rest -split '/')[0]
        if (($Top -like "*.dist-info") -or ($Top -like "*.egg-info")) {
            $Top = ($Top -split '-')[0]
        }
        $Base = $Top.ToLower().Replace('-', '_')
        foreach ($Token in $ForbiddenTokens) {
            if (($Base -eq $Token) -or ($Base -like "$Token*")) {
                $ForbiddenEntryHits += $Key
                break
            }
        }
    }
    if ($ForbiddenEntryHits.Count -gt 0) {
        $ForbiddenEntryHits | Select-Object -First 10 | ForEach-Object { Write-Host "      $_" }
        throw "产物里混进了 $($ForbiddenEntryHits.Count) 个模型后端/numba 条目——这不是精简包，是残包。"
    }

    # 密钥与运行时垃圾：config.json（API 密钥）、logs/、模型权重。
    if ($Entries.ContainsKey("$DestName/config/config.json")) {
        throw "产物里混进了 config/config.json（含 API 密钥）。"
    }
    $LogHits = @($Entries.Keys | Where-Object { $_ -like "$DestName/logs/*" })
    if ($LogHits.Count -gt 0) {
        throw "产物里混进了 logs/。"
    }
    $WeightHits = @($Entries.Keys | Where-Object {
        ($_ -like "$DestName/data/models/*") -and ($WeightExtensions -contains ([System.IO.Path]::GetExtension($_).ToLower()))
    })
    if ($WeightHits.Count -gt 0) {
        throw "产物里混进了模型权重：$($WeightHits[0])"
    }
}

# ---------------------------------------------------------------------------
# 1. 预检：源一致性 + 原生资产（先跑，避免删掉上一份产物才发现版本不一致）
# ---------------------------------------------------------------------------
Step 1 "预检：源一致性、manifest/版本、原生资产"

foreach ($Required in @($RequirementsFile, $PyprojectFile, $ManifestFile, (Join-Path $RepoRoot "launch.py"))) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "缺少必需文件：$Required（本脚本必须在 git 工作树内运行）"
    }
}

# uv 必须是钉定版本，不允许 latest 重定向。
if ($UvUrl -match "/latest/") {
    throw "拒绝 latest 下载：uv 必须钉定版本（-UvVersion 默认 $UvVersion，或用 -UvUrl 指到具体版本/镜像）。"
}

# —— 版本一致性（无条件硬校验，任何模式都不跳过）——
$PyprojectVersion = ""
foreach ($Line in (Get-Content -LiteralPath $PyprojectFile -Encoding UTF8)) {
    $Trimmed = $Line.Trim()
    if ($Trimmed -match '^version\s*=\s*"([^"]+)"') {
        $PyprojectVersion = $Matches[1]
        break
    }
}
if (-not $PyprojectVersion) {
    throw "无法从 pyproject.toml 读到 [project] version。"
}
$Manifest = Get-Content -LiteralPath $ManifestFile -Encoding UTF8 -Raw | ConvertFrom-Json
$ManifestVersion = $Manifest.version
if (-not $ManifestVersion) {
    throw "manifest.json 没有 version 字段。"
}
if ($ManifestVersion -ne $PyprojectVersion) {
    throw @"
版本不一致：pyproject.toml = $PyprojectVersion，manifest.json = $ManifestVersion。
  发版前先跑 scripts/generate_manifest.py 重新生成清单并提交，再构建。
"@
}
Write-Host "      版本一致：pyproject.toml = manifest.json = $PyprojectVersion"

# —— 跟踪文件清单（后续复制与计数都用它）——
$TrackedFiles = @(Invoke-GitLines @("ls-files"))
if (-not $TrackedFiles -or $TrackedFiles.Count -eq 0) {
    throw "git ls-files 没有返回任何文件——本脚本必须在 git 工作树内运行。"
}

# —— 发行门禁：工作区干净 + manifest 覆盖/哈希（-AllowDirtyWorkTree 放宽）——
if ($AllowDirtyWorkTree) {
    Write-Warning "-AllowDirtyWorkTree：跳过「工作区干净」与「manifest 覆盖/哈希」两项发行门禁。该产物不得用于发行。"
} else {
    $Dirty = @(Invoke-GitLines @("status", "--porcelain"))
    if ($Dirty.Count -gt 0) {
        $Dirty | Select-Object -First 20 | ForEach-Object { Write-Host "      $_" }
        throw @"
工作区不干净（$($Dirty.Count) 项改动），拒绝据此生成发行包。
  先提交/暂存，或改用 -AllowDirtyWorkTree 做调试构建（该产物不得发行）。
"@
    }
    Write-Host "      工作区干净"

    $ManifestFiles = @($Manifest.files.PSObject.Properties.Name)
    $TrackedForGate = @($TrackedFiles | Where-Object { $_ -ne "manifest.json" })
    $MissingInManifest = @($TrackedForGate | Where-Object { $ManifestFiles -notcontains $_ })
    $ExtraInManifest = @($ManifestFiles | Where-Object { $TrackedForGate -notcontains $_ })
    if ($MissingInManifest.Count -gt 0 -or $ExtraInManifest.Count -gt 0) {
        $MissingInManifest | Select-Object -First 10 | ForEach-Object { Write-Host "      manifest 缺：$_" }
        $ExtraInManifest | Select-Object -First 10 | ForEach-Object { Write-Host "      manifest 多：$_" }
        throw "manifest.json 与 git ls-files 的文件集合不一致——重新跑 scripts/generate_manifest.py 再构建。"
    }

    $HashMismatch = @()
    foreach ($Rel in $TrackedForGate) {
        $Full = Join-Path $RepoRoot $Rel
        if (-not (Test-Path -LiteralPath $Full -PathType Leaf)) {
            $HashMismatch += "$Rel（磁盘上不存在）"
            continue
        }
        $Prop = $Manifest.files.PSObject.Properties[$Rel]
        if (-not $Prop) { continue }
        # manifest.json 的值形如 "sha256:<小写hex>"（scripts/generate_manifest.py::compute_sha256），
        # Get-FileHash 返回大写裸 hex——比较前先归一，否则发行门禁逐文件误报。
        $Actual = (Get-FileHash -LiteralPath $Full -Algorithm SHA256).Hash.ToLower()
        $Expected = "$($Prop.Value)".ToLower() -replace '^sha256:', ''
        if ($Actual -ne $Expected) {
            $HashMismatch += $Rel
        }
    }
    if ($HashMismatch.Count -gt 0) {
        $HashMismatch | Select-Object -First 10 | ForEach-Object { Write-Host "      内容不符：$_" }
        throw "manifest.json 与实际源码内容不一致（$($HashMismatch.Count) 个文件）——重新跑 scripts/generate_manifest.py 再构建。"
    }
    Write-Host "      manifest 覆盖 $($ManifestFiles.Count) 个跟踪文件且哈希全部一致"
}

# —— 原生资产（无条件硬校验：缺任一 DLL 就拒绝产出"看着完整但 PatchMatch 不可用"的包）——
foreach ($Asset in $NativeAssets) {
    if (-not (Test-Path -LiteralPath $Asset.Path -PathType Leaf)) {
        throw @"
原生资产缺失：$($Asset.Path)
  精简包必须随包携带 data/libs 下的两个 DLL（PatchMatch 是基础修复能力）。
  源码运行/重新构建时请先从网盘的一键包中取回 data/libs/ 下的这几个文件。
  也可以用 -PatchMatchDll / -OpencvWorldDll 指到别处的副本。
"@
    }
    $Item = Get-Item -LiteralPath $Asset.Path
    if ($Item.Length -le 0) {
        throw "原生资产为空文件：$($Asset.Path)"
    }
    if ((Split-Path -Leaf $Asset.Path) -ne $Asset.Name) {
        throw "原生资产文件名不符：期望 $($Asset.Name)，实际 $(Split-Path -Leaf $Asset.Path)（运行时要按 data/libs/$($Asset.Name) 加载）。"
    }
    $Asset.Size = $Item.Length
    $Asset.Sha256 = (Get-FileHash -LiteralPath $Asset.Path -Algorithm SHA256).Hash.ToLower()
    Write-Host "      原生资产 $($Asset.Name)：$(Format-Size $Asset.Size)  sha256=$($Asset.Sha256.Substring(0,12))…"
}

Write-Host "      跟踪源码 $($TrackedFiles.Count) 个文件；Python $PythonVersion；uv $UvVersion"

if ($StaticCheck) {
    Write-Host ""
    Write-Host "=== 离线预检通过（-StaticCheck，未下载、未产出）==="
    Write-Host "计划打包成分："
    Write-Host "  源码：(git ls-files) $($TrackedFiles.Count) 个跟踪文件（含 docs/tests/scripts）"
    Write-Host "  运行时：嵌入式 Python $PythonVersion + pip + uv $UvVersion"
    Write-Host "  依赖：requirements.txt 预装到 ballontrans_pylibs_win\Lib\site-packages"
    Write-Host "  原生：data/libs/patchmatch_inpaint.dll、data/libs/opencv_world455.dll"
    Write-Host "  排除：$($ForbiddenPackages -join ' / ')、data/models 权重、config/config.json、logs/"
    Write-Host "  预期体积：约 550–600 MB（压缩后；实际以产物为准）"
    Write-Host "  模型后端与权重由 GUI 按需补全；PatchMatch 是随包的基础能力。"
    Write-Host ""
    Write-Host "正式构建请去掉 -StaticCheck（默认发行门禁要求工作区干净）。"
    foreach ($Scratch in @("git_out.txt", "git_err.txt")) {
        $P = Join-Path $BuildDir $Scratch
        if (Test-Path -LiteralPath $P) { Remove-Item -Force $P }
    }
    exit 0
}

# ---------------------------------------------------------------------------
# 2. 清理上一次的产物
# ---------------------------------------------------------------------------
Step 2 "清理上一次的产物"
if (Test-Path -LiteralPath $BuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}
if (Test-Path -LiteralPath $Output) {
    Remove-Item -Force $Output
}
$OutputParent = Split-Path -Parent $Output
if ($OutputParent -and -not (Test-Path -LiteralPath $OutputParent)) {
    New-Item -ItemType Directory -Force -Path $OutputParent | Out-Null
}
New-Item -ItemType Directory -Force -Path $PyLibsDir | Out-Null
Write-Host "      $DestDir"

# ---------------------------------------------------------------------------
# 3. 源码（仅跟踪文件）
# ---------------------------------------------------------------------------
Step 3 "复制跟踪的源码文件（git ls-files）"
$Copied = 0
$Missing = @()
foreach ($Rel in $TrackedFiles) {
    $Src = Join-Path $RepoRoot $Rel
    if (-not (Test-Path -LiteralPath $Src -PathType Leaf)) {
        $Missing += $Rel
        continue
    }
    $Dst = Join-Path $DestDir $Rel
    $DstParent = Split-Path -Parent $Dst
    if (-not (Test-Path -LiteralPath $DstParent)) {
        New-Item -ItemType Directory -Force -Path $DstParent | Out-Null
    }
    Copy-Item -LiteralPath $Src -Destination $Dst -Force
    $Copied++
}
Write-Host "      已复制 $Copied / $($TrackedFiles.Count) 个跟踪文件"
if ($Missing.Count -gt 0) {
    # 跟踪文件在磁盘上缺失 = 工作树处于半途状态（删了没暂存、合并冲突……）。
    # 静默跳过会把残包发出去，比在这里停下更糟——调试构建也不放宽这条。
    $Missing | ForEach-Object { Write-Host "      缺失：$_" }
    throw "$($Missing.Count) 个跟踪文件在磁盘上不存在——拒绝生成不完整的包。"
}

# ---------------------------------------------------------------------------
# 4. 原生资产：PatchMatch + OpenCV world
# ---------------------------------------------------------------------------
Step 4 "放入原生资产 data/libs/"
$DllDestDir = Join-Path $DestDir "data\libs"
New-Item -ItemType Directory -Force -Path $DllDestDir | Out-Null
foreach ($Asset in $NativeAssets) {
    Copy-Item -LiteralPath $Asset.Path -Destination (Join-Path $DllDestDir $Asset.Name) -Force
    Write-Host "      data/libs/$($Asset.Name)  $(Format-Size $Asset.Size)"
}

# ---------------------------------------------------------------------------
# 5. 嵌入式 Python
# ---------------------------------------------------------------------------
Step 5 "下载并解压嵌入式 Python $PythonVersion"
$PyZipPath = Join-Path $BuildDir "python-$PythonVersion-embed-amd64.zip"
Get-RemoteFile -Url $PythonEmbedUrl -Destination $PyZipPath -What "嵌入式 Python $PythonVersion"
Assert-FileSha256 -Path $PyZipPath -Expected $PythonZipSha256 -What "python-$PythonVersion-embed-amd64.zip"
Expand-Archive -Path $PyZipPath -DestinationPath $PyLibsDir -Force
Remove-Item -Force $PyZipPath

$PyExe = Join-Path $PyLibsDir "python.exe"
if (-not (Test-Path -LiteralPath $PyExe)) {
    throw "解压后没有 python.exe：$PyLibsDir"
}
# 归档 URL 可能被 -PythonEmbedUrl 换成了别的版本，这里核对真身。
$ReportedVersion = (& $PyExe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "staging 的 python.exe 无法运行（$PyExe）"
}
if ($ReportedVersion -ne $PythonVersion) {
    throw "嵌入式解释器版本不符：期望 $PythonVersion，实际 $ReportedVersion（检查 -PythonEmbedUrl 是否指向对应版本）。"
}
Write-Host "      $PyExe 报版本 $ReportedVersion"

# ---------------------------------------------------------------------------
# 6. 在 ._pth 里打开 site（pip 装的包才可导入）
# ---------------------------------------------------------------------------
Step 6 "在嵌入式 Python 的 ._pth 里启用 site-packages"
$Parts = $PythonVersion.Split(".")
$PthFile = Join-Path $PyLibsDir ("python" + $Parts[0] + $Parts[1] + "._pth")
if (-not (Test-Path -LiteralPath $PthFile)) {
    throw "找不到 $PthFile"
}
$PthUpdated = @()
foreach ($Line in (Get-Content -LiteralPath $PthFile)) {
    if ($Line.Trim() -eq "#import site") { $PthUpdated += "import site" } else { $PthUpdated += $Line }
}
if (-not ($PthUpdated | Where-Object { $_.Trim() -eq "import site" })) {
    throw "$PthFile 里没有 import site / #import site 行，无法启用 site-packages。"
}
Set-Content -LiteralPath $PthFile -Value $PthUpdated -Encoding ascii
Write-Host "      import site 已启用"

# ---------------------------------------------------------------------------
# 7. pip
# ---------------------------------------------------------------------------
Step 7 "向嵌入式 Python 安装 pip"
$GetPipPath = Join-Path $BuildDir "get-pip.py"
Get-RemoteFile -Url $GetPipUrl -Destination $GetPipPath -What "get-pip.py"
& $PyExe $GetPipPath --no-warn-script-location
if ($LASTEXITCODE -ne 0) {
    throw "get-pip.py 失败（exit $LASTEXITCODE）"
}
Remove-Item -Force $GetPipPath
& $PyExe -m pip --version
if ($LASTEXITCODE -ne 0) {
    throw "pip 安装后不可用。"
}

# ---------------------------------------------------------------------------
# 8. uv（钉定版本；launch.py 在 python.exe 旁边找它）
# ---------------------------------------------------------------------------
Step 8 "取 uv $UvVersion 放到 python.exe 旁边"
$UvZipPath = Join-Path $BuildDir "uv.zip"
$UvTempDir = Join-Path $BuildDir "uv_temp"
Get-RemoteFile -Url $UvUrl -Destination $UvZipPath -What "uv $UvVersion"
Assert-FileSha256 -Path $UvZipPath -Expected $UvZipSha256 -What "uv-$UvVersion"
Expand-Archive -Path $UvZipPath -DestinationPath $UvTempDir -Force
$UvSrc = Get-ChildItem -LiteralPath $UvTempDir -Filter "uv.exe" -Recurse | Select-Object -First 1
if (-not $UvSrc) {
    throw "下载的归档里没有 uv.exe：$UvUrl"
}
$UvExe = Join-Path $PyLibsDir "uv.exe"
Copy-Item -LiteralPath $UvSrc.FullName -Destination $UvExe -Force
Remove-Item -Force $UvZipPath
Remove-Item -Recurse -Force $UvTempDir
& $UvExe --version
if ($LASTEXITCODE -ne 0) {
    throw "uv.exe 不可执行：$UvExe"
}

# ---------------------------------------------------------------------------
# 9. 用 staging 的 uv/pip 预装 requirements.txt
# ---------------------------------------------------------------------------
Step 9 "安装 requirements.txt 到 staging 环境"
# 绝不允许构建机上的用户级 site-packages 混进来：否则"核心 import 检查"会在
# 构建机上通过，而包里其实缺包。
$env:PYTHONNOUSERSITE = "1"
# uv 只许用 staging 的解释器，不许自己去下 Python。
$env:UV_PYTHON_DOWNLOADS = "never"

# uv 与 pip 的镜像参数拼写不同，不能互相抄：uv 只认 --default-index
# （--index-url 在 0.12 已标记 deprecated），pip 只认 --index-url。
# uv 也不接受 pip 的 --no-warn-script-location（0.12.19 实测报
# "unexpected argument"）——把 pip 的参数抄给 uv 会让主装路静默退化成 pip。
$UvIndexArgs = @()
$PipIndexArgs = @()
if ($IndexUrl) {
    $UvIndexArgs = @("--default-index", $IndexUrl)
    $PipIndexArgs = @("--index-url", $IndexUrl)
    Write-Host "      使用 pip 源：$IndexUrl"
}

$Installed = $false
$UvInstallArgs = @("pip", "install", "--python", $PyExe, "-r", $RequirementsFile) + $UvIndexArgs
Write-Host "      uv pip install --python <staging python> -r requirements.txt"
& $UvExe @UvInstallArgs
if ($LASTEXITCODE -eq 0) {
    $Installed = $true
} else {
    Write-Warning "uv 安装失败（exit $LASTEXITCODE），回退到 staging 自带的 pip（结果等价，只是慢一些）。"
}
if (-not $Installed) {
    $PipInstallArgs = @("-m", "pip", "install", "-r", $RequirementsFile, "--prefer-binary", "--disable-pip-version-check", "--no-warn-script-location") + $PipIndexArgs
    & $PyExe @PipInstallArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pip install -r requirements.txt 失败（exit $LASTEXITCODE）。"
    }
}

if (-not (Test-Path -LiteralPath $SitePackages)) {
    throw "安装后仍没有 $SitePackages —— uv/pip 没有装进 staging 环境。"
}
$SitePackageCount = @(Get-ChildItem -LiteralPath $SitePackages -Directory -Force).Count
if ($SitePackageCount -lt 10) {
    throw "site-packages 里只有 $SitePackageCount 个目录，依赖安装明显没成功。"
}
Write-Host "      site-packages：$SitePackageCount 个目录，$(Format-Size ((Get-ChildItem -LiteralPath $SitePackages -File -Recurse -Force | Measure-Object -Property Length -Sum).Sum))"

# ---------------------------------------------------------------------------
# 10. staging 校验：核心 import + 精简包成分
# ---------------------------------------------------------------------------
Step 10 "校验 staging 环境（核心 import + 精简包成分）"

# 核心 import 的权威判据来自仓库自身：直接调
# utils/core_requirements.py::check_core_imports，避免在这里复制一份探针清单。
$VerifyScript = Join-Path $BuildDir "verify_staged_env.py"
Set-Content -LiteralPath $VerifyScript -Encoding UTF8 -Value @'
import sys

repo_root, site_packages = sys.argv[1], sys.argv[2]
# Mirror launch.py at runtime: the embedded interpreter's own site-packages must
# be importable even if site.main() alone did not add it.
if site_packages not in sys.path:
    sys.path.append(site_packages)
sys.path.insert(0, repo_root)

from utils.core_requirements import check_core_imports

failures = check_core_imports()
if failures:
    print("staged environment is missing required packages:")
    for line in failures:
        print(line)
    sys.exit(1)
print("core imports OK")
'@
& $PyExe $VerifyScript $RepoRoot $SitePackages
if ($LASTEXITCODE -ne 0) {
    throw @"
staging 环境的核心 import 校验未通过——精简包会带着缺包发布。
  上一条命令列出了缺哪些包：requirements.txt 装完后仍缺，说明依赖声明或
  安装源有问题，修好再构建（不要靠构建机的用户级 site-packages 蒙过去）。
"@
}

# 允许普通 site-packages，但模型后端/加速一个都不许进精简包。
$StagedForbidden = @(Get-ForbiddenHitsInDir -Directory $SitePackages)
if ($StagedForbidden.Count -gt 0) {
    throw @"
精简包里出现了不该有的重依赖：$($StagedForbidden -join ' / ')
  模型后端（torch/ultralytics/onnxruntime/onnxocr/transformers）与 numba 由
  GUI 按需安装，不能预装进精简包。requirements.txt 不该把它们拉进来——
  检查是谁的传递依赖（多半是 requirements.txt 或 extra 配置被改过）。
"@
}
Write-Host "      没有模型后端/numba：$($ForbiddenPackages -join ' / ')"

# 任何模型权重都不许进包（跟踪清单里只有 data/models 的两个占位/字典文件）。
$StagedWeights = @(Get-ChildItem -LiteralPath (Join-Path $DestDir "data\models") -File -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $WeightExtensions -contains $_.Extension.ToLower() })
if ($StagedWeights.Count -gt 0) {
    $StagedWeights | Select-Object -First 10 | ForEach-Object { Write-Host "      权重：$($_.FullName)" }
    throw "data/models 下出现了模型权重文件——精简包不携带任何权重。"
}

# config.json（API 密钥）与 logs 绝不能入包——这是当年删掉 build_portable.py 的原因。
$ConfigLeak = Join-Path $DestDir "config\config.json"
if (Test-Path -LiteralPath $ConfigLeak) {
    throw "拒绝打包：config/config.json（含 API 密钥）会被发出去。"
}
if (Test-Path -LiteralPath (Join-Path $DestDir "logs")) {
    throw "拒绝打包：logs/ 不应出现在发行包里。"
}
if (-not (Test-Path -LiteralPath (Join-Path $DestDir "launch.py"))) {
    throw "staging 里缺 launch.py。"
}

# 每个跟踪文件都必须落地（外加两个原生资产）。这条检查最便宜，也正是当年
# 能抓住乱码路径 bug 的那条。
$StagedCount = @(Get-ChildItem -LiteralPath $DestDir -File -Recurse -Force |
    Where-Object { $_.FullName -notmatch '[\\/]ballontrans_pylibs_win[\\/]' }).Count
$ExpectedCount = $TrackedFiles.Count + $NativeAssets.Count
if ($StagedCount -ne $ExpectedCount) {
    throw "staging 里有 $StagedCount 个源码/资产文件，期望 $ExpectedCount（$($TrackedFiles.Count) 个跟踪文件 + $($NativeAssets.Count) 个原生资产）——包会不完整或混入多余文件。"
}
Write-Host "      文件计数 OK：$StagedCount（$($TrackedFiles.Count) 跟踪 + $($NativeAssets.Count) 原生资产）"

# ---------------------------------------------------------------------------
# 11. 打包 + 产物结构校验
# ---------------------------------------------------------------------------
Step 11 "打包并校验 ZIP 结构"
Write-Host "      创建 $Output ..."
try {
    # ZipArchiveMode 住在 System.IO.Compression 里：Windows PowerShell 5.1 上只
    # 加载 System.IO.Compression.FileSystem 解析不到这个类型（实测报"找不到类型
    # [System.IO.Compression.ZipArchiveMode]"，随后 ZipFile::Open 返回 null），
    # 两个程序集都要显式加载。
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
} catch {
    # PowerShell 7 已默认加载。
}
$ArchiveRoot = (Resolve-Path -LiteralPath $DestDir).Path
$Zip = [System.IO.Compression.ZipFile]::Open($Output, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $ArchiveRoot -File -Recurse -Force |
        Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
        ForEach-Object {
            $RelativePath = $_.FullName.Substring($ArchiveRoot.Length + 1).Replace('\', '/')
            $EntryName = "$DestName/$RelativePath"
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $Zip, $_.FullName, $EntryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
} finally {
    $Zip.Dispose()
}

# —— 打开产物逐条核对（不是"看起来打包成功"就算过）——
Assert-PackageArchive -ArchivePath $Output

# —— 体积统计 ——
$PyLibsBytes = (Get-ChildItem -LiteralPath $PyLibsDir -File -Recurse -Force | Measure-Object -Property Length -Sum).Sum
$NativeBytes = ($NativeAssets | Measure-Object -Property Size -Sum).Sum
$DestBytes = (Get-ChildItem -LiteralPath $DestDir -File -Recurse -Force | Measure-Object -Property Length -Sum).Sum
$SourceBytes = $DestBytes - $PyLibsBytes - $NativeBytes
$SizeMb = [math]::Round((Get-Item -LiteralPath $Output).Length / 1MB, 1)

if (-not $KeepBuildDir) {
    Remove-Item -Recurse -Force $BuildDir
}

Write-Host ""
Write-Host "=== 构建完成（精简包）==="
Write-Host "产物：      $Output"
Write-Host "压缩后：    $SizeMb MB（精简包预期约 550–600 MB；实际以产物为准）"
Write-Host "构成：      源码 $(Format-Size $SourceBytes) | 嵌入式环境 $(Format-Size $PyLibsBytes) | 原生 DLL $(Format-Size $NativeBytes)"
Write-Host "运行时：    嵌入式 Python $PythonVersion + pip + uv $UvVersion；requirements.txt 已预装"
Write-Host "原生能力：  data/libs/patchmatch_inpaint.dll（PatchMatch 基础修复，开箱可用）"
Write-Host "            data/libs/opencv_world455.dll"
Write-Host "不含：      $($ForbiddenPackages -join ' / ')、模型权重、config/config.json、logs/"
Write-Host ""
Write-Host "模型后端与权重由 GUI 按需补全："
Write-Host "  后端：选中模块时由 modules/base.py::ensure_dependencies 安装"
Write-Host "  权重：设置 → Models → 模型文件（后台下载，进度只进终端）"
Write-Host "  GPU： CUDA 版 torch/onnxruntime 仍由 install_cuda.bat 负责（精简包之外的操作）"
Write-Host "没有可用网络的用户仍走网盘上 ~1.7 GB 的预装完整包（离线兜底）。"
if ($SizeMb -gt 800) {
    Write-Warning "产物 $SizeMb MB 明显超出精简包预期——检查是否混入了未列在禁用清单里的重依赖。"
}
