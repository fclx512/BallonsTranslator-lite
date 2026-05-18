# BallonsTranslator-lite

> **注意**：本项目正处于高频重构阶段，代码改动频繁且幅度较大，README 暂无法同步说明所有修改。待重构稳定后将统一更新文档。

基于 BallonsTranslator 的轻量化漫画翻译工具，保留完整嵌字管线，大幅削减体积与冗余模块，支持纯 CPU 离线运行。

## 功能特性

- **一键翻译管线**：文本检测 → OCR → 翻译 → 修图(抹字) → 译文嵌字回填，支持批量处理
- **图像编辑**：修复画笔、选区工具（矩形/套索）、蒙版编辑
- **富文本编辑**：所见即所得的译文排版，支持字体、字号、颜色、描边、对齐、行距等调整
- **字体样式预设**：可保存和复用字体风格配置
- **查找替换**：支持页面内搜索、全局搜索替换（全文/原文/译文）
- **条漫支持**：多页连续阅读与翻译，导出 Word 文档

## 精简说明

相对于上游 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator)，本分支主要做了以下精简：

- **翻译器**：移除了百度、彩云、DeepL、DeepLX、Google、有道、Papago、Sugoi、M2M100、Yandex 等翻译引擎（个人认为机翻在非即时场景已不适合与大模型竞争，故移除接口）
  - 保留并新增了一些目前主流的大模型 API 翻译接口（注意：国内某些 API 服务商存在安全围栏，会拒绝敏感内容输入）
- **OCR 引擎**：移除了 PaddleOCR、Google Vision、Bing Lens、macOS/Windows 原生 OCR、Manga OCR、OneOCR、Stariver 等不常用或效果较差的接口
  - 本地 OCR 方案默认使用 MIT48px-CTC，保留 LLM API OCR 方案，可接入支持视觉能力的大模型 API
- **修图**：移除了原项目的 Flux Inpaint 管线（依赖重、体积大）和其他无明显优势的模型，保留效果最好的 `lama_large_512px` 和占用低的 AOT
- **其他**：移除 Saladict 查词集成、关键词替换子面板、连续无头模式（HEADLESS_CONTINUOUS）、系统 HuggingFace 缓存选项等
- **依赖**：精简 requirements.txt，移除了 keyboard、deeplx、saladict 等非必要依赖

完整项目包括模型、依赖库以及 CPU 版 PyTorch 环境约 **2.3 GB**，可快速打包移动。

## 新增与优化

- **纯 CPU 离线运行**：捆绑 CPU 版 PyTorch，通过 `launch_cpu.bat` 或 `--cpu` 参数即可在无 GPU 环境下运行本地模型
- **交互优化**：字体面板布局重组，改善字体设置操作流；移除冗余配置项
- **Python 3.13**：随附环境升级至 Python 3.13
- **CPU/GPU 双启动**：提供 `launch_win.bat`（GPU）和 `launch_cpu.bat`（CPU）两种启动方式
- **字体选择增强**：字体菜单新增字重/样式（Regular、Bold、Italic 等）独立选择
- **小功能优化持续更新中…**

## 快速开始

注意：原项目有 macOS 和 AMD 显卡支持，但本人没有相关设备无法测试兼容性，故本项目默认只支持 Windows 的 CUDA 或 CPU 运行。

不要下载微软商店版的 Python。WindowsApps 目录会留下 `python.exe` 占位符，即使卸载后该路径仍会触发跳转。若已安装，请在搜索引擎检索 `Python 打开 Windows 商店` 寻找处理教程。

### Windows 一键启动

1. 下载源码，解压到本地目录
2. 运行 `launch_win.bat`（GPU 模式）或 `launch_cpu.bat`（纯 CPU 模式）
3. 首次启动会自动下载模型文件，请保持网络畅通

### 源码运行

```bash
# 克隆仓库
git clone https://github.com/yourname/BallonsTranslator-lite.git
cd BallonsTranslator-lite

# 安装依赖并启动（GPU 模式）
python launch.py

# 或 CPU 模式（即使有 GPU 也强制 CPU）
python launch.py --cpu
```

更新代码请在项目根目录下运行：`python launch.py --update`

首次运行会自动安装 PyTorch 等依赖并下载模型文件（约 700MB）。若下载失败，需手动将 `data` 目录放置到项目根目录。遇到网络问题可在此下载模型和依赖库：【链接示例】

GPU 模式下默认读取用户系统的 PyTorch（需含 CUDA），若未安装会在控制台提示并自动退回 CPU 模式。
