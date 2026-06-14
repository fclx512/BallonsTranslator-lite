# Model Hub 实现现状 (2026-06-14)

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `ui/model_hub.py` | 新建 | ModelHub 核心组件：StatusDot, ModelInfoDialog, ModelStatusRow, ModelHub |
| `ui/configpanel.py` | 修改 | 用 ModelHub 替换旧的 "Models" GroupBox |
| `ui/module_manager.py` | 修改 | 添加 model_status_changed 信号及状态查询 |

## 架构

### ModelHub 在 ConfigPanel 中的位置

```
ConfigBlock "DL Module"
├── PanelGroupBox "Model Hub"       ← NEW: ModelHub 总览面板
├── PanelGroupBox "Text Detection"   ← 原有: 参数面板
├── PanelGroupBox "OCR"             ← 原有: 参数面板
├── PanelGroupBox "Inpaint"         ← 原有: 参数面板
└── PanelGroupBox "Translator"      ← 原有: 参数面板
```

### ModelHub 内部结构

```
ModelHub (QWidget)
├── ModelStatusRow × 4 (textdetector, ocr, inpainter, translator)
│   ├── StatusDot (彩色状态点)
│   ├── QLabel 阶段名 (bold)
│   ├── QLabel 模块名
│   ├── QLabel 状态文字
│   ├── ⓘ 按钮 → ModelInfoDialog
│   └── → 按钮 → 跳转到对应参数面板
├── QFrame 分割线
├── QLabel 依赖摘要栏
├── checkbox "Load models on demand"
├── checkbox "Empty cache after RUN"
├── [Unload All] 按钮
└── [Manage API Profiles...] 按钮
```

### 状态系统

**6 种状态值**（`model_hub.py` 模块级常量）：

| 常量 | 显示文字 | 图标 | 颜色 | 触发条件 |
|------|---------|------|------|----------|
| `S_NONE` | "Not configured" | ○ | #999 | 模块名以 "none" 开头 |
| `S_IDLE` | "Idle" | ◌ | #888 | 已选中模块，满足所有条件，未加载 |
| `S_LOADED` | "Loaded" | ● | #2ecc71 | `module.all_model_loaded() == True` |
| `S_DEPS` | "Deps missing" | ● | #e67e22 | `requires_packages` 中有未安装的包 |
| `S_DL` | "Needs download" | ◐ | #f39c12 | `download_file_list` 中有文件不存在 |
| `S_UNKNOWN` | "Unknown" | ? | #ccc | registry 中找不到模块 |

**判断逻辑**（`_module_status()` 函数）：
1. 模块名以 `none` 开头 → S_NONE
2. 不在 registry 中 → S_UNKNOWN
3. 检查 `requires_packages` 是否全部安装 → 有缺则 S_DEPS
4. 检查 `download_file_list` 文件是否全部存在 → 有缺则 S_DL
5. 以上都通过 → S_IDLE（"已加载"需要 ModuleManager 运行时信息覆盖）

**状态覆盖**：`ModelHub._update_loaded_status()` 在有 `ModuleManager` 引用时，将 `all_model_loaded() == True` 的模块覆盖为 S_LOADED。

## 信号流

```
用户切换模块下拉框
  → ModuleConfigParseWidget.module_changed (Qt Signal)
  → ModuleManager.setXxx() → ModuleThread._set_module()
  → ModuleThread.finish_set_module (Qt Signal)
  → ModuleManager.model_status_changed
  → ModelHub.refresh()

用户点击 Unload All
  → ConfigPanel.unload_models (Qt Signal)
  → ModuleManager.unload_all_models()
  → ModuleManager.model_status_changed
  → ModelHub.refresh()

用户初次打开配置面板
  → ConfigPanel.setupConfig()
  → ModelHub.refresh()
```

## 关键代码入口

### `model_hub.py`

- **`_module_status(module_key: str) -> tuple`** — 核心状态判定函数。读取 `pcfg.module.{key}` 获取当前模块名，查 registry 获取 `requires_packages` 和 `download_file_list` 做静态检查。返回 `(status, installed_pkgs, missing_pkgs, files_ok, files_total)`。
- **`ModelHub.set_module_manager(manager)`** — 接受 `ModuleManager` 引用用于获取运行时加载状态。
- **`ModelHub.refresh()`** — 遍历所有行刷新，构建摘要文字（文件统计 + 缺失依赖）。

### `configpanel.py`

替换了 `ConfigPanel.__init__` 中原本创建旧 "Models" GroupBox 的代码段（L1289-L1303）。保留了 `self.load_model_checker` / `self.empty_runcache_checker` 作为 ModelHub 内部 checkbox 的引用别名，保证现有 `on_load_model_changed` / `on_runcache_changed` / `setupConfig` 零改动。

### `module_manager.py`

- `ModuleManager` 新增 `model_status_changed = Signal()`（L957）
- `ModuleManager.unload_all_models()` 末尾 emit signal（L1087）
- `ModuleManager.get_module_loaded(key) -> bool`（L1090-1096）
- 4 个线程的 `finish_set_module` 均连接 `model_status_changed`
- `setupThread()` 末尾连接 ModelHub + 4 个模块面板的 `*_changed` 信号 → `hub.refresh`

## 未实现（待后续）

1. **下载进度条** — 当前下载是同步的（`download_url_to_file`），没有信号/回调机制。需要改造 `_ensure_model_files()` 或 `download_util.py` 加入进度报告。
2. **每模块独立 Load/Unload 按钮** — 目前只有 "Unload All" 全局按钮。
3. **"Load All" 按钮** — 已从界面移除（点击不会有效果）。模块默认懒加载，在首次运行时自动加载。
4. **内存使用统计** — 没有监控各模型实际占用的 GPU 显存。
5. **Device 统一显示** — 目前 Model Hub 不展示各模块使用的 device（参数面板里有各自的 device 选择器）。
