# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

<!-- SCREENSHOT: 主界面全景 -->

基于 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的轻量化漫画/图片翻译工具。

> **注意**：项目正处于高频重构阶段，说明可能不是最新。如有出入以实际行为为准。

---

## 关于本项目

[BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的多页嵌字管线效率很高，这是 fork 它的原因。但上游在长期维护中积累了相当多个人看来实用性下降的功能，选择多不等于好用。

本分支做的事：**保留核心嵌字能力，砍掉理解成本高的东西，优化交互体验**

### 精简内容

**翻译器**：上游有翻译接口繁多，但在非实时场景下传统机翻质量已经明显落后于大模型。已改版为一个统一化便于编辑的 LLM API 和一个离线备选（Sakura）。

**OCR**：同理，质量差/老旧的方案挂在菜单里不是多个选择，是多个坑。有些或许不错，但部署基本都过分复杂/体积大且没有达到100%准确率。故只保留了本地 MIT48px-CTC 和可选的 LLM API OCR。

**修图**：保留了适用性相对较好的 lama_large_512px 和性能开销低的 AOT 。其余管线删减理由同上，依赖重、体积大，边际收益太低。

**辅助功能**：查词、关键词替换、无头模式——使用频率极低，或者交互逻辑独立一套，维护成本和实际收益不成比例。这些功能的存在本身会分散对核心流程的注意力，删掉对普通用户而言是净收益。

### 交互

按照个人理解优化了部分流程和操作交互，目前（5/31）暂没有精力详细描述，后续会更新文档以及讲解视频。

### 硬件

捆绑 CPU 版 PyTorch，没有独显也能跑完整管线。有 GPU 自动检测架构代际，无需手动配环境。RTX 50 系列自动切 CUDA 12.8+ nightly，旧卡同样识别并提示合适的 CUDA 版本。

本项目含模型、CPU PyTorch 和保证所有功能正常运行的依赖库总共约 **1.8 GB**。支持压缩打包快速切换设备运行
理论上是可以去掉模型和CPU PyTorch实现更极限的精简运行的，但背景修复没有合适的处理方案无法跑通流程故没有细化该需求


## 快速开始

### Windows 一键启动

1. 下载源码，解压到目录
2. 运行启动脚本：
   - `launch_win_update.bat` — GPU 模式，启动前自动检查更新（推荐）
   - `launch_win.bat` — GPU 模式，跳过更新检查
   - `launch_cpu.bat` — 纯 CPU 模式，跳过更新检查
3. 首次启动自动下载模型文件（约 700 MB），保持网络畅通

因网络问题无法正常下载依赖和模型的移步至网盘：
[google盘](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing)
[123盘](https://1815181720.share.123865.com/123pan/sKBtVv-Zs1Vd)
使用方法：将下载后的两个压缩包解压至项目根目录
解压后示意：

BallonsTranslator-lite/
├── ballontrans_pylibs_win/
└── data/

### 源码运行

```bash
git clone https://github.com/fclx512/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU 模式：自动检测系统 PyTorch + CUDA，未安装则退回 CPU
python launch.py

# CPU 模式：强制使用 CPU
python launch.py --cpu

# 更新代码
python launch.py --update
```

> 上游支持 macOS 和 AMD 显卡，因缺少设备测试，本分支只面向 Windows CUDA / CPU 环境。

---

## 功能概览

待施工……

## 模块一览

| 阶段 | 可用模块 |
|------|----------|
| 文字检测 | CTD（默认）、YSG |
| OCR | MIT48px-CTC（默认）、LLM API OCR、LM Studio、关闭 OCR |
| 翻译 | LLM API（OpenAI 兼容）、Sakura |
| 修图 | LaMa 512px（默认）、AOT |

> 想添加自己的模块？参见 [模块开发指南](docs/how_to_add_new_translator.md)

---

## 常见问题

**RTX 50 系列 CUDA 不可用？**

Blackwell 架构需要 CUDA 12.8+。手动重装：

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**老显卡（GTX 10 系列等）CUDA 不可用？**

Maxwell/Pascal 等旧架构建议尝试 CUDA 11.8：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

Kepler（GTX 6xx / 7xx）可能不被 PyTorch 2.x 支持，建议 CPU 模式：`python launch.py --cpu`

**如何更新？**

- 便携版用户：使用 `launch_win_update.bat` 启动，每次自动检查 GitHub 更新，有新版时下载并在下次启动应用。无需 git。
- 源码运行用户：`python launch.py --update`（优先 git pull，无 git 时自动回退到直接下载）
- 或在软件中点击关于→检查更新

**快捷键怎么自定义？**

参见 [快捷键指南](docs/shortcuts.md)

---

## 致谢

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — 上游项目
- 项目使用的所有开源模型和库
