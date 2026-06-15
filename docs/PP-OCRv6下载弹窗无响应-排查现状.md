# PP-OCRv6 下载弹窗无响应排查现状

## 原始问题

选择 OCR 模块为 PP-OCRv6 ONNX（`paddleocr_v6_onnx`）后，弹出依赖安装弹窗（`_InstallDialog`），点击 "Install All" 后窗口无响应，下载没有开始。

**原因分析：** 该模块的模型文件托管在 HuggingFace（`huggingface.co`），国内网络无法正常访问；同时 pip 安装 `onnxruntime` / `onnxocr` 时 `subprocess.run()` 阻塞 GUI 线程，无任何进度反馈。

## 已做的改动

### 1. `utils/download_util.py` — 加下载超时

`download_url_to_file()` 新增 `timeout` 参数（默认 30s），传入 `urlopen()`。HuggingFace 被屏蔽时 30 秒超时报错，不再永久挂起。

### 2. `ui/module_manager.py` — 弹窗改进

- **HF 镜像提示**：检测到模型 URL 包含 `huggingface.co` 且 `pcfg.mirror.hf_endpoint` 为空时，显示橙色警告框指导用户去 Settings → Mirror Config 配置 `https://hf-mirror.com`
- **状态文字**：在按钮和进度条之间加了 `_status_label`，显示当前操作（"Installing Python packages…" / "Downloading det.onnx …"）
- **下载异常兜底**：模型下载外包裹 `try/except`，异常时记日志并设 `success = False`，弹窗显示失败 + Retry 按钮
- **pip --timeout 30**：传给 pip/uv 的网络超时参数

## 当前遗留问题

### A. GUI 线程阻塞（未解决）

`subprocess.run()` 和 `download_and_check_files()` 都在 GUI 线程中阻塞执行。虽然加了 `processEvents()` 和状态文字让用户看到"即将开始"，但执行期间窗口仍然冻结。

**尝试过的方案：** 用 `subprocess.Popen` + 轮询 + `QApplication.processEvents()`（每 100ms）替代 `subprocess.run`。效果是窗口不卡死了，但弹窗关闭后整个主界面的刷新速率被污染，出现异常。

**原因推测：** `QApplication.processEvents()` 在模态弹窗的按钮事件处理器中频繁调用，弹窗关闭后主事件循环时序被破坏。

### B. 镜像配置无法自动生效

用户需要手动去 Settings → Mirror Config 设置 `hf_endpoint` 为 `https://hf-mirror.com`。即使设置了，`download_url_to_file()` 中的 `maybe_mirror_url()` 才真正替换 URL。目前已有"中文用户预设"功能（自动填充国内源）。

## 建议的后续方向

### 方案 1：QThread 后台下载（推荐）

将 pip 安装和模型下载迁移到 `QThread`，通过信号更新进度：

```
_do_install() → 启动 InstallWorker(QThread)
                → pip install 在子进程（保持 subprocess）
                → 模型下载用 requests 流式 + 信号报告进度
                → 完成后发射 finished(success) 信号
                → 主线程关闭弹窗
```

### 方案 2：QProcess 替代 subprocess

用 `QProcess` 启动 pip，通过 `readyReadStandardOutput` / `finished` 信号非阻塞交互，无需轮询。

### 方案 3：离线包（最简单）

不通过网络下载，将 PP-OCRv6 ONNX 模型文件（det.onnx + rec.onnx，约 20MB）直接打包进仓库 `data/models/ppocrv6_onnx/`，同时把 `download_file_list` 置空。

## 相关文件

| 文件 | 关键代码 |
|------|----------|
| `ui/module_manager.py:772-1013` | `_ensure_module_deps()` + `_InstallDialog` |
| `modules/ocr/ocr_onnx.py:84-93` | PP-OCRv6 ONNX 模块的 `download_file_list` |
| `utils/download_util.py:123-220` | `download_url_to_file()` — 已加 30s 超时 |
| `utils/mirror.py:9-37` | `maybe_mirror_url()` — HF URL 镜像替换逻辑 |
| `utils/config.py:123-132` | `MirrorConfig` — `hf_endpoint` 配置定义 |
