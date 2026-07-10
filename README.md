# BallonsTranslator-lite

[简体中文](README.md) | [English](README_EN.md)

基于 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的精简分支，专注漫画/图片翻译核心管线。

---

## 与本家差异

### 模块精简

当前可用模块（与上游对比）：

| 管线阶段 | lite 可用模块 | 上游额外模块（lite 未包含） |
|---------|--------------|--------------------------|
| 文字检测 | CTD、YSG | 同 |
| OCR | MIT48px-CTC、LLM API OCR、LM Studio、关闭 | PaddleOCR、Google Vision、Bing Lens 等 |
| 翻译 | LLM API（OpenAI 兼容）、Sakura | Baidu、Caiyun、DeepL、Google、Youdao、Papago 等传统机翻 |
| 修图 | LaMa 512px、AOT、Lama MPE | — |

其他已精简功能：
- 查词（Saladict）
- 关键词替换
- 无头模式

### 交互差异

| 项目 | lite | 上游 |
|------|------|------|
| 设置面板 | 内部分页 + 中心模态（OverlayModal），遮罩仅覆盖中央画布区 | 右侧长卷轴滚动面板 |
| 左侧面板（PageList/全局搜索） | 展开时推 canvas 右移，零遮挡 | 浮层遮挡画布 |
| 关于页面 | Help 菜单（关于 + MCP 信息） | 独立 About 对话框 |
| 文本框重排 | 右键菜单 + 快捷键 | 面板操作 |

### 部署差异

- **捆绑 CPU PyTorch**：无 NVIDIA 显卡也可运行完整管线
- **GPU 自动检测**：自动识别显卡架构代际，匹配 CUDA 版本（RTX 50 系列自动切 CUDA 12.8+）
- **嵌入式 Python 环境**：`ballontrans_pylibs_win/` 自包含 Python 3.12 + 全部依赖，不依赖系统 Python
- **不支持跨 Python ABI 注入 torch**：不再尝试在不同 Python 版本间共享 site-packages

> 上游有 macOS 和 AMD 显卡支持。本分支因缺少测试设备，仅面向 Windows x64 CUDA / CPU 环境。

---

## 部署

### 系统要求

- **操作系统**：Windows 10+ x64
- **显卡**：可选（NVIDIA GPU 加速支持，纯 CPU 可运行）
- **磁盘空间**：约 **2.1 GB**（含模型文件 ~700 MB、嵌入式 Python 环境 ~1.4 GB）
- **VC++ 运行时**：[VC++ Redistributable 2015-2022](https://aka.ms/vs/17/release/vc_redist.x64.exe)（embedded Python 依赖）

### Windows 一键启动

1. 下载源码（ZIP 或 git clone），解压到本地目录
2. 运行启动脚本：
   - `launch.bat` — 自动检测 GPU/CPU 模式（推荐）
   - `launch.bat --cpu` — 强制 CPU 模式
3. 首次启动自动下载模型文件（约 700 MB），保持网络畅通

网络问题无法下载的，可从网盘获取完整包：

- [Google Drive](https://drive.google.com/drive/folders/1WJXjcQt7UzHvRpH3QfwcOokL8Fm7l0zT?usp=sharing) （更新有可能延迟，优先选择123盘）
- [123 云盘](https://1815181720.share.123865.com/123pan/sKBtVv-Zs1Vd)

使用方法：将下载后的压缩包解压至项目根目录，得到：

```
BallonsTranslator-lite/
├── ballontrans_pylibs_win/   # 嵌入式 Python 环境
└── data/                     # 模型文件
```

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

首次启动自动安装依赖。如自动安装失败，可手动执行：

```bash
pip install -r requirements.txt
```

### 模型下载

模型文件在首次启动时自动下载。如需手动准备，将模型文件放入 `data/models/` 目录。

### GPU 加速

#### 一键包用户（嵌入式 Python）

运行 `install_cuda.bat` 将 CUDA PyTorch 安装到嵌入式 Python 环境：

```cmd
install_cuda.bat
```

脚本自动检测 GPU 计算能力并选择对应 CUDA 版本（支持10系以上，更老的架构性能较低故不做一键下载支持，请自行测试兼容性）

#### 源码运行用户

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

**Kepler（GTX 6xx / 7xx）** 不被 PyTorch 2.x 支持，请使用 CPU 模式：`python launch.py --cpu`

### 更新

- **ZIP 发行版用户**：`launch.bat` 启动时自动检查 GitHub 更新，新版在下次启动时应用
- **Git 用户**：
  - 使用启动脚本：`launch.bat --update`
  - 直接运行 Python：`python launch.py --update`
- **应用内**：Help → About → 检查更新

## 常见问题

**如何自定义快捷键？**

参见 [快捷键指南](docs/快捷键.md)

**如何添加翻译 API？**

在设置面板 → Models → API Profiles 中配置。

**MCP 如何使用？**

参见 [MCP 用户指南](docs/MCP用户指南.md)

---

## 致谢

- [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) — 上游项目
- 项目使用的所有开源模型和库
