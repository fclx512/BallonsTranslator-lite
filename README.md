# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

BallonsTranslator的分支版漫画/图片翻译工具。五阶段管线：文字检测 → OCR → 翻译 → 图像修复 → 文字渲染。

---

## 系统要求

- **操作系统**：Windows 10+ x64
- **显卡**：可选（NVIDIA GPU 加速，纯 CPU 可运行）
- **磁盘空间**：约 **2.1 GB**（含模型文件 ~700 MB、嵌入式 Python 环境 ~1.4 GB）
- **VC++ 运行时**：[VC++ Redistributable 2015-2022](https://aka.ms/vs/17/release/vc_redist.x64.exe)

## 快速开始

### 一键包（推荐）

从以下渠道下载完整依赖和模型（含嵌入式 Python 3.12 + 全部依赖 + 模型文件），解压后运行 `launch.bat`：

- [123 云盘](https://1815181720.share.123865.com/123pan/sKBtVv-Zs1Vd)（优先）
- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing)（更新可能有延迟）

也可仅下载轻量包（不含模型文件），首次启动自动下载约 700 MB 模型：

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # 嵌入式 Python 环境（约 1.4 GB）
└── launch.bat
```

### 源码运行

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU 模式（自动检测 CUDA，未安装则退回 CPU）
python launch.py

# 强制 CPU 模式
python launch.py --cpu

# 更新代码
python launch.py --update
```

首次启动自动安装依赖。如自动安装失败：

```bash
pip install -r requirements.txt
```

### 模型文件

首次启动自动下载到 `data/models/`。也可从网盘完整包中提取后放入该目录。

## GPU 加速

### 一键包用户

运行 `install_cuda.bat` 将 CUDA PyTorch 安装到嵌入式 Python 环境：

```cmd
install_cuda.bat
```

脚本自动检测 GPU 计算能力并匹配 CUDA 版本（10 系以上支持）。

### 源码运行用户

确保系统 Python 已安装 CUDA 版 PyTorch：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**RTX 50 系列（Blackwell）** 需要 CUDA 12.8+：

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**老显卡（GTX 10 系列等）** 建议 CUDA 11.8：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

**Kepler（GTX 6xx / 7xx）** 不被 PyTorch 2.x 支持，请使用 CPU 模式。

## 更新

- **ZIP 发行版用户**（一键包）：前往 [Releases](https://github.com/fclx512/BallonsTranslator-lite/releases) 下载最新版源码压缩包，解压覆盖到原目录。一键包不含 git，无法通过启动脚本或应用内功能更新。
- **Git 用户**：`python launch.py --update` 或 `git pull`
- **应用内检查**：Help → About → 检查更新（需系统已安装 git，仅获取提交记录，不自动更新）

## 致谢

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — 上游项目
- 项目使用的所有开源模型和库
