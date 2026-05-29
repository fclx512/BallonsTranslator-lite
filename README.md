# BallonsTranslator-lite

注意：本项目正处于高频重构阶段，README暂时由ai撰写，代码改动频繁且幅度较大，可能无法同步说明所有修改。待重构稳定后将人工更新文档。


基于 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的轻量化漫画/图片翻译工具，保留完整嵌字管线，大幅削减体积与冗余模块，支持纯 CPU 离线运行。

## 功能特性

### 翻译管线

- **一键翻译管线**：文字检测 → OCR → 翻译 → 修图（抹字）→ 译文嵌字回填，支持批量处理与页面范围选择
- **纯 CPU 离线运行**：捆绑 CPU 版 PyTorch，无需 GPU 即可运行本地模型。GPU 模式下自动检测用户系统已安装的 PyTorch（含 CUDA），未安装时自动退回 CPU

### 图像编辑

- **修复画笔**：涂抹需要抹除的文字区域
- **选区工具**：矩形选区、套索选区，批量抹除
- **蒙版编辑**：手动调整文字区域蒙版

### 文字排版

- **所见即所得**：画布上直接编辑译文，支持字体、字号、颜色、描边、对齐、行距、字间距等调整
- **投影/渐变**：PS 风格钟表盘控件，直观调节投影角度、距离、模糊度和渐变方向
- **字体样式预设**：保存和复用字体风格配置，支持局部覆盖（仅应用部分属性）
- **字体筛选**：排除不常用字体，精简字体列表

### 搜索替换

- **页面内搜索**：当前页面查找替换
- **全局搜索**：左侧滑入面板，跨页面搜索（全文/原文/译文）
- **跨页批量替换**：一键替换所有页面中的匹配文本

### AI 助手

- **自然语言交互**：通过聊天面板用自然语言操控项目，支持修改译文、调整样式、查询文字块等操作
- 支持 OpenAI 兼容 API，模型与参数均可配置

### 其他

- **主题切换**：内置多套配色主题，支持浅色/深色模式
- **键盘快捷键**：可自定义快捷键，支持保存/加载快捷键配置
- **导出**：支持导出文本文件 (TXT)
- **条漫阅读**：多页连续阅读与翻译
- **页面预览模式**：快速预览原文/译文对照

## 模块清单

### 文字检测

| 模块 | 说明 |
|------|------|
| CTD (默认) | CNN 文字检测，快速准确 |
| YSG | YOLO-based 文字检测 |

### OCR

| 模块 | 说明 |
|------|------|
| MIT48px-CTC (默认) | 本地 OCR，无需 GPU |
| LLM API OCR | 调用视觉大模型 API 进行 OCR |
| LM Studio | 本地 LM Studio 接入 |
| None | 关闭 OCR（手动输入原文） |

### 翻译

| 模块 | 说明 |
|------|------|
| LLM API | 通用大模型 API 翻译接口，支持 OpenAI 兼容协议 |
| Sakura | Sakura 翻译模型 |

### 修图（Inpainting）

| 模块 | 说明 |
|------|------|
| LaMa 512px (默认) | 效果最好，抹字干净 |
| AOT | 轻量快速，占用低 |

## 精简说明

相对于上游 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator)，本分支主要做了以下精简：

- **翻译器**：移除了百度、彩云、DeepL、DeepLX、Google、有道、Papago、Sugoi、M2M100、Yandex 等翻译引擎（非即时场景下机翻已不适合与大模型竞争）
  - 保留并新增主流大模型 API 翻译接口（注意：国内某些 API 服务商存在安全围栏，会拒绝敏感内容输入）
- **OCR 引擎**：移除了 PaddleOCR、Google Vision、Bing Lens、macOS/Windows 原生 OCR、Manga OCR、OneOCR、Stariver 等不常用或效果较差的接口
  - 本地 OCR 方案使用 MIT48px-CTC，保留 LLM API OCR 和 LM Studio 接入
- **修图**：移除了 Flux Inpaint 管线（依赖重、体积大）和其他无明显优势的模型，保留效果最好的 LaMa 512px、轻量的 AOT
- **其他**：移除 Saladict 查词集成、关键词替换子面板、连续无头模式、系统 HuggingFace 缓存选项等
- **依赖**：精简 requirements.txt，移除 keyboard、deeplx、saladict 等非必要依赖

完整项目包括模型、依赖库以及 CPU 版 PyTorch 约 **1.8 GB**，可快速打包移动。

## 快速开始

> 注意：上游项目支持 macOS 和 AMD 显卡，因缺少设备测试，本分支只面向 Windows CUDA / CPU 环境。
> 项目附带上游的 macOS 构建脚本可自行测试可行性（`scripts/build-macos-app.sh`、`scripts/macos-build-script-arm64.sh`），因上条理由故无法提供技术支持

不要下载微软商店版的 Python。WindowsApps 目录会留下 `python.exe` 占位符，即使卸载后仍会触发跳转。若已安装，请在搜索引擎检索 `Python 打开 Windows 商店` 寻找处理教程。

### Windows 一键启动

1. 下载源码，解压到本地目录
2. 运行 `launch_win.bat`（GPU 模式）或 `launch_cpu.bat`（纯 CPU 模式）
3. 首次启动会自动下载模型文件，请保持网络畅通

### 源码运行

```bash
git clone https://github.com/dmMaze/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# GPU 模式（自动检测系统 PyTorch + CUDA，未安装则退回 CPU）
python launch.py

# CPU 模式（强制使用捆绑的 CPU 版 PyTorch）
python launch.py --cpu

# 更新代码
python launch.py --update
```

首次运行会自动安装 PyTorch 等依赖并下载模型文件（约 700MB）。若下载失败，需手动将 `data` 目录放置到项目根目录。

GPU 模式会自动检测用户系统 Python 中已安装的 PyTorch（需含 CUDA）。若检测到 RTX 50 系列（Blackwell）GPU，会自动切换至 CUDA 12.8+ nightly 版本。

## 使用说明

### 基本流程

1. 打开包含漫画/图片的文件夹
2. 在配置面板中选择源语言和目标语言
3. 点击"Run"按钮，等待管线执行完成
4. 在画布上双击文字块编辑不满意的译文

### 译文编辑

- 双击画布上的文字块进入编辑模式
- 右侧字体面板调整字体、字号、颜色、描边等属性
- 可保存文字样式为预设，快速应用

### 修图工具

- 使用修复画笔涂抹需要恢复的区域
- 使用矩形/套索工具框选需要批量清除的区域

## FAQ

**PyTorch + CUDA 没检测到？**
确认系统 Python 已安装 PyTorch with CUDA：

```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

**RTX 50 GPU CUDA 不可用？**
Blackwell 架构需要 CUDA 12.8+，应用已自动使用 nightly 版本。手动重装：

```bash
pip uninstall torch torchvision torchaudio ultralytics -y
python launch.py --reinstall-torch
```

**如何更新？**
在项目根目录运行 `python launch.py --update`。
