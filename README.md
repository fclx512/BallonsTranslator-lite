# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

BallonsTranslator 的分支版漫画/图片翻译工具。上游的五阶段管线（文字检测 → OCR → 翻译 → 图像修复 → 文字渲染）完整保留，改动集中在：缩短常用操作路径，用不上的精简（个人取向）

---

## 用前须知

**开发方式** 本仓库 100% 由 vibe coding 开发与维护，作者无法保证项目稳定性，也未对每一种用法都做过验证。崩溃、启动失败等恶性问题请在 issue 里反馈并附上终端输出。同时欢迎积极提供体验反馈以供改进

**功能展示等待施工中……**

## 系统要求

- **操作系统**：Windows 10+ x64（macOS 见下文，其他平台未验证）
- **显卡**：可选。NVIDIA 显卡可加速（见 [GPU 加速](#gpu-加速)），纯 CPU 可运行
- **磁盘空间**：推荐的网盘完整包约 **1.7–2.1 GB**（含匹配的 Python 环境、模型后端和部分模型）；GPU 完整包按配置更大。约 30 MB 的轻量包需要显式联网修复依赖，仅适合有网络的维护者/高级用户。
- **VC++ 运行时**：[VC++ Redistributable 2015-2022 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)（嵌入式 Python 依赖它）

## 快速开始

### 一键完整包（推荐）

从网盘下载含依赖与模型的完整包，解压到任意目录后运行 `launch.bat`：

- [139 移动云盘](https://yun.139.com/shareweb/#/w/i/2xTrJSfJTx8m8)（提取码：`8xys`）
- [百度网盘](https://pan.baidu.com/s/1QfPub2loKQ7Zp9lyhYtSnA?pwd=1111)（提取码：`1111`）
- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing)（更新可能有延迟）

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # 嵌入式 Python 3.12 + 全部依赖（约 1.4 GB）
├── data/                     # 模型文件、PatchMatch 原生库（约 700 MB）
├── config/                   # 配置与主题（config.json 首次运行时生成）
├── ui/  modules/  utils/     # 程序源码
└── launch.bat                # 双击启动
```

`launch.bat` 只使用包内的 `ballontrans_pylibs_win/python.exe`，不依赖系统 Python、PATH 或用户 site-packages；缺少该环境时会提示重新解压匹配的完整包。它会判断可用 GPU 并选择 CPU/GPU 路径。

完整包带有与源码相匹配的 Python 环境及已装依赖/模型后端，解压后可离线启动；模型权重以包内实际内容为准，缺失权重仍可在 GUI 的模型文件页下载。CPU 与 CUDA 包应选择对应档位，不要在 CPU 包上临时拼装不同 CUDA 组件。

### 轻量发版包（维护者/高级用户）

约 30 MB 的轻量包只含源码和嵌入式 Python，不预装核心依赖、模型后端或权重；普通启动不会自动联网补装。只有明确同意在线修复时才运行 `launch.bat --repair-deps`。首次使用仍需网络下载依赖，模型与部分原生资产也需另行取得。普通用户和离线用户请使用上面的网盘完整包。

从 [Releases 页面](https://github.com/fclx512/BallonsTranslator-lite/releases/latest) 下载 `BallonsTranslator-lite_win_min.zip`；它是维护者引导包，不等同于完整发行环境。构建方式见 `scripts/build_win_minimal.ps1` 与 `scripts/README.md`「轻量包构建」一节。

### 源码运行

Windows 源码用户也建议下载匹配版本的网盘完整包，将其中的 `ballontrans_pylibs_win/` 解压到仓库根目录，再使用 `launch.bat`。不要依赖系统 Python 作为默认或回退环境。

```cmd
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite
rem 将匹配的 ballontrans_pylibs_win/ 放在此目录后：
launch.bat --cpu
launch.bat --update
```

- 没有配套环境时，核心依赖缺失会停止并给出提示；只有明确选择在线修复才运行 `launch.bat --repair-deps`。轻量包首启联网安装不是推荐安装方式。
- 所有依赖与模型下载默认走**官方源**；国内网络如需镜像，在 **设置 → 网络与镜像** 手动配置（`config/config.json` 的 `mirror` 节）。
- 模型推理依赖及模型文件与 CPU/CUDA 档位有关；优先获取匹配的完整环境包，不建议在发行环境中手工混装。
- **PatchMatch 修复器**是低占用基础能力，其两个原生库（`data/libs/patchmatch_inpaint.dll` 与 `data/libs/opencv_world455.dll`，约 53 MB）以 Release 资产分发：首次选中该修复器时后台自动下载，也可在 **设置 → 模型 → 模型文件** 页手动获取（一键完整包已随包）。缺失时应用仍可启动，但使用 PatchMatch 时会给出明确提示。
- 完整参数见 `python launch.py --help`（如 `--proj-dir` 启动时打开工程、`--headless` 无界面运行）。

## GPU 加速

仅 NVIDIA 显卡可用（AMD / Intel 显卡走 CPU）。普通用户应下载与显卡档位匹配的 CUDA 完整包。`install_cuda.bat` 是联网维护工具：默认只检查并显示环境方案，不会安装；确认要修改包内环境后才运行：

```cmd
install_cuda.bat --install
```

`install_cuda.bat` 默认只读检查；`install_cuda.bat --json` 输出机器可读检查结果，也不修改环境。`--install` 会联网更换包内 PyTorch/ONNX Runtime 组件，属于维护者操作；完成后必须重新检查运行与发行包清单。该工具只操作 `ballontrans_pylibs_win`，不会安装到系统 Python 或任意 venv。

索引按显卡计算能力选择（阈值与 `utils/env_diagnostic.py` 中的映射同一套）：

| 显卡                                   | 计算能力        | CUDA 索引   |
| ------------------------------------ | ----------- | --------- |
| GTX 10 系 / GTX 16 系 / RTX 20·30·40 系 | 6.x – 8.9   | `cu126`   |
| Hopper 等数据中心卡                        | 9.x         | `cu130`   |
| RTX 50 系（Blackwell）                  | 10.x / 12.x | `cu132`   |
| Kepler（GTX 6xx / 7xx）及更早             | < 6         | 不支持，用 CPU |

自行维护的 CUDA 环境请按官方 PyTorch 安装页选择匹配命令，并独立验证 Torch、TorchVision 和 ONNX Runtime 组合；版本配对以官方说明为准：

[PyTorch 官方版本配对说明](https://pytorch.org/get-started/previous-versions/)

两个索引不要用：

- **`cu124`**：索引里最后一个版本是 torch 2.6.0，而本项目基线是 torch 2.13.x，用它会把已装好的 torch 静默降级。
- **`nightly/cu128`**：能装，但 nightly 是每晚构建的通道、版本天天漂；CC 12.x 请用稳定索引 `cu132`。

## 模型文件

- 权重不入库，统一落在 `data/models/`。
- 选中需要权重的模块时自动下载；标注为大模型的（如 PaddleOCR-VL）改为后台下载，进度只进终端，不弹窗、不阻断操作。
- 需要 GPU 的模型在没有可用加速设备的机器上会被直接拒绝下载，并说明原因。
- **设置 → 模型管理 → 「模型文件」**&#x53EF;查看各模块的落盘状态与占用体积，手动下载 / 取消 / 删除（删除走回收站）。

## 更新

- **应用内（推荐）**：**设置 → 应用 → 更新 → 「检查更新」**。发现新版本会弹出发布说明，确认后自动下载并替换程序源码，完成后重启；`data/`、模型、配置与嵌入式环境不受影响。勾选「启动时检查更新」可在启动时静默检查。
- **开发者通道**：同一节的「检查提交更新」跟随最新提交，属未验证改动，可能在某些机器上不可用。
- **一键包（无 git）**：`launch.bat --update` 按 manifest 增量拉取改动的源文件，重启后自动应用（`launch.bat --check-update` 只检查）。也可直接下载新版源码压缩包覆盖原目录。
- **Git 安装**：`python launch.py --update` 或 `git pull`。

## macOS

**没有在 Mac 上做过验证，不保证可用**，也没有一键包。源码运行：

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install torch torchvision transformers diffusers   # Apple Silicon 走 MPS 加速
python launch.py
```

- 需要 Python 3.10 以上，推荐 3.12（一键包内置 3.12；更高的 3.13 及以上未经验证）。
- **Apple Silicon（M 系列）**：PyTorch 自动使用 MPS 加速，无需 CUDA；Intel Mac 或不支持 MPS 时退回 CPU。
- `launch.bat`、`install_cuda.bat` 为 Windows 专用。
- PatchMatch 的原生库只提供 Windows 版（Release 资产选中即自动下载，一键完整包已随包）；macOS 没有对应库，改用基于 PyTorch 的修复器。

## 致谢

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — 上游项目
- 本项目使用的所有开源模型与库

## 许可

[GPL-3.0](LICENSE)，与上游一致。
