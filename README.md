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
- **磁盘空间**：一键包约 **2.1 GB**（嵌入式 Python 环境 ~1.4 GB + 模型文件 ~700 MB）；启用 GPU 加速再加约 **2.2 GB**（CUDA PyTorch ~2 GB + onnxruntime-gpu ~214 MB）
- **VC++ 运行时**：[VC++ Redistributable 2015-2022 x64](https://aka.ms/vs/17/release/vc_redist.x64.exe)（嵌入式 Python 依赖它）

## 快速开始

### 一键包（推荐）

从网盘下载含依赖与模型的完整包，解压到任意目录后运行 `launch.bat`：

- [123 云盘](https://1815181720.share.123865.com/123pan/sKBtVv-Zs1Vd)（优先）
- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing)（更新可能有延迟）

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # 嵌入式 Python 3.12 + 全部依赖（约 1.4 GB）
├── data/                     # 模型文件与原生库（约 700 MB）
├── config/                   # 配置与主题（config.json 首次运行时生成）
├── ui/  modules/  utils/     # 程序源码
└── launch.bat                # 双击启动
```

`launch.bat` 会自行寻找可用的 Python（内置环境优先）、判断是否有可用 GPU，并区分一键包与 git 安装两种形态，无需手工配置。

下载大包不便时，可用不含模型文件的精简包：解压后同样运行 `launch.bat`，缺少的依赖在首次启动时自动补装，模型权重在选中对应模块时下载。

### 源码运行

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

python launch.py            # 首次启动自动补装缺失依赖后重启
python launch.py --cpu      # 强制 CPU 模式
python launch.py --update   # 先拉取代码更新再启动
```

- 需要 **Python 3.10+**（官方安装包或 Microsoft Store 版均可；Store 版会在项目目录内自动创建 `.venv`）。
- 首次启动会按所在地区自动写入 pip 镜像（`config/config.json` 的 `mirror` 节），国内网络无需手工配源。
- 自动装依赖失败时手动执行 `pip install -r requirements.txt`。模型推理所需的 `torch` / `transformers` 等重依赖不在其中，按需安装（见 [GPU 加速](#gpu-加速)）。
- **PatchMatch 修复器**依赖 `data/libs/` 下的原生库，该目录不入 git：源码运行时需要从一键包中取这几个文件；缺失时改用基于 PyTorch 的修复器。
- 完整参数见 `python launch.py --help`（如 `--proj-dir` 启动时打开工程、`--headless` 无界面运行）。

## GPU 加速

仅 NVIDIA 显卡可用（AMD / Intel 显卡走 CPU）。**一键包用户**直接运行：

```cmd
install_cuda.bat
```

脚本会检测显卡计算能力、匹配对应的 CUDA 索引，把 CUDA 版 PyTorch（约 2 GB）与 onnxruntime-gpu（约 214 MB）装进嵌入式 Python 环境；已装好的部分自动跳过。装完照常用 `launch.bat` 启动，GPU 模式自动生效。

- `install_cuda.bat --manual`：只打印命令，不装任何东西（源码运行、自建 venv、conda 环境用这个）
- `install_cuda.bat --replace`：强制装进一键包的嵌入式环境

索引按显卡计算能力选择（阈值与 `utils/env_diagnostic.py` 中的映射同一套）：

| 显卡 | 计算能力 | CUDA 索引 |
| --- | --- | --- |
| GTX 10 系 / GTX 16 系 / RTX 20·30·40 系 | 6.x – 8.9 | `cu126` |
| Hopper 等数据中心卡 | 9.x | `cu130` |
| RTX 50 系（Blackwell） | 10.x / 12.x | `cu132` |
| Kepler（GTX 6xx / 7xx）及更早 | < 6 | 不支持，用 CPU |

**源码运行**不要执行 `install_cuda.bat`（它默认装进一键包的内置环境），在自己的环境里装：

```bash
pip install -U torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

也可用 `install_cuda.bat --manual` 打印与本机显卡匹配的命令，或 `python launch.py --reinstall-torch` 让启动脚本按显卡自动选索引重装。

两个索引不要用：

- **`cu124`**：索引里最后一个版本是 torch 2.6.0，而本项目基线是 torch 2.13.x，用它会把已装好的 torch 静默降级。
- **`nightly/cu128`**：能装，但 nightly 是每晚构建的通道、版本天天漂；CC 12.x 请用稳定索引 `cu132`。

## 模型文件

- 权重不入库，统一落在 `data/models/`。
- 选中需要权重的模块时自动下载；标注为大模型的（如 PaddleOCR-VL）改为后台下载，进度只进终端，不弹窗、不阻断操作。
- 需要 GPU 的模型在没有可用加速设备的机器上会被直接拒绝下载，并说明原因。
- **设置 → 模型管理 → 「模型文件」**可查看各模块的落盘状态与占用体积，手动下载 / 取消 / 删除（删除走回收站）。

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
- PatchMatch 修复器缺少 macOS 原生库，不可用，改用基于 PyTorch 的修复器。

## 致谢

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — 上游项目
- 本项目使用的所有开源模型与库

## 许可

[GPL-3.0](LICENSE)，与上游一致。
