# BallonsTranslator-lite 设置面板参考

> 本文档对应设置面板的所有功能项。面板分为两大区域：**DL Module**（深度学习模块设置）和 **General**（通用设置），左侧为树状导航，右侧为详细设置区域。

---

## 一、DL Module 区域

### 全局模块设置

位于文本检测/OCR/修复/翻译四个子区域之上。

| 显示名 | 类型 | 说明 |
|--------|------|------|
| Load models on demand | 勾选框 | 需要时才加载模型，节省内存 |
| Empty cache after RUN | 勾选框 | 每次运行管线后清空缓存 |
| Unload All Models | 按钮 | 立即卸载所有已加载模型 |

---

### 1. 文本检测 (Text Detection)

选择器下拉框可切换 **ComicTextDetector** 和 **YSG-YOLO** 两个检测器。

#### ComicTextDetector (CTD)

| 参数 | 类型 | 默认值 | 取值 | 说明 |
|------|------|--------|------|------|
| detect_size | 下拉框 | 1280 | 896 / 1024 / 1152 / 1280 | 检测分辨率，越大越耗时但能检测到更小文本 |
| det_rearrange_max_batches | 下拉框 | 4 | 1/2/4/6/8/12/16/24/32 | 重排最大批处理数 |
| device | 下拉框 | (自动) | CPU/CUDA 等 | 运行设备 |
| font size multiplier | 输入框 | 1.0 | 浮点数 | 检测到的字号缩放系数 |
| font size max | 输入框 | -1 | 整数 | 最大检测字号（-1=不限制） |
| font size min | 输入框 | -1 | 整数 | 最小检测字号（-1=不限制） |
| mask dilate size | 输入框 | 2 | 整数 | 区域遮罩扩张像素 |

#### YSG-YOLO

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| Model Path | 下拉框+文件选择 | `data/models/ysgyolo_1.2_OS1.0.pt` | 模型文件路径，可手动输入或浏览选择 |
| Merge Text Lines | 勾选框 | True | 合并在同一行的文本块 |
| Confidence Threshold | 输入框 | 0.3 | 检测置信度阈值（浮点） |
| IoU Threshold | 输入框 | 0.5 | IoU 阈值（浮点） |
| Font Size Multiplier | 输入框 | 1.0 | 检测字号缩放系数 |
| Max Font Size | 输入框 | -1 | 最大检测字号（-1=不限制） |
| Min Font Size | 输入框 | -1 | 最小检测字号（-1=不限制） |
| Detection Size | 输入框 | 1024 | 检测分辨率 |
| Device | 下拉框 | (自动) | 运行设备 |
| Label | 勾选组 | 全部勾选 | 要检测的文本区域类型：`balloon`(对话泡), `qipao`(气泡), `shuqing`(竖排), `changfangtiao`(长条), `hengxie`(横写), `other`(其他) |
| Vertical Text | 勾选框 | True | 是否检测竖排文字 |
| Mask Expansion Size | 输入框 | 2 | 区域遮罩扩张像素 |

---

### 2. OCR (文字识别)

#### MIT48pxCTC (OCR_MIT 32px)

| 参数 | 类型 | 默认值 | 取值 | 说明 |
|------|------|--------|------|------|
| chunk_size | 下拉框 | 16 | 8 / 16 / 24 / 32 | 每次传入的切片数量，越大越快但更耗显存 |
| device | 下拉框 | (自动) | CPU/CUDA 等 | 运行设备 |

#### LLM OCR (多厂商视觉 OCR)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| provider | 下拉框 | OpenAI | 厂商：OpenAI / Google / OpenRouter / Ollama |
| api_key | 输入框 | (空) | API 密钥（未启用多 key 时使用） |
| multiple_keys | 多行文本框 | (空) | 多个密钥，用分号 `;` 分隔，自动轮换 |
| endpoint | 输入框 | (空) | 自定义 API 地址，留空使用厂商默认 |
| model | 下拉框 | OAI: gpt-4o-mini | 选择模型（各厂商前缀不同） |
| override_model | 输入框 | (空) | 手动覆盖模型名 |
| language | 下拉框 | Japanese | OCR 目标语言 |
| detail_level | 下拉框 | auto | 图像细节级别：auto / low / high |
| prompt | 多行文本框 | (OCR 提示词模板) | 发给模型的 OCR 指令，`{language}` 为语言占位符 |
| system_prompt | 多行文本框 | (系统角色提示) | 系统角色设定 |
| proxy | 输入框 | (空) | 代理地址，格式 `http(s)://user:password@host:port` |
| delay | 输入框(浮点) | 1.0 | 请求间隔（秒） |
| requests_per_minute | 输入框(整数) | 15 | 每分钟每密钥最大请求数 |
| max_response_tokens | 输入框(整数) | 4096 | 最大响应 token 数 |

#### LMStudio OCR (本地视觉模型)

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| endpoint | 输入框 | `http://localhost:1234/v1` | LM Studio 服务地址 |
| model | 输入框 | (空) | 加载的视觉模型名（如 `qwen2-vl-7b`） |
| language | 下拉框 | Japanese | OCR 目标语言 |
| prompt | 多行文本框 | (OCR 提示词模板) | OCR 指令，`{language}` 占位符 |
| system_prompt | 多行文本框 | (系统角色提示) | 系统角色设定 |
| proxy | 输入框 | (空) | 代理地址 |
| delay | 输入框(浮点) | 0.5 | 请求间隔（秒） |
| max_response_tokens | 输入框(整数) | 4096 | 最大响应 token 数 |

#### None OCR (透传)

| 参数 | 说明 |
|------|------|
| NOTICE | 这是一个占位 OCR，直接返回原始文本，不做任何识别 |

---

### 3. 文字修复 (Inpaint)

选择器下拉框可切换 **AOT**、**Lama MPE**、**Lama Large** 等修复器。

#### AOT Inpainter

| 参数 | 类型 | 默认值 | 取值 | 说明 |
|------|------|--------|------|------|
| inpaint_size | 下拉框 | 2048 | 1024 / 2048 | 修复分辨率，越大质量越高越慢 |
| device | 下拉框 | (自动) | CPU/CUDA 等 | 运行设备 |

#### Lama MPE

| 参数 | 类型 | 默认值 | 取值 | 说明 |
|------|------|--------|------|------|
| inpaint_size | 下拉框 | 2048 | 1024 / 2048 | 修复分辨率 |
| device | 下拉框 | (自动) | CPU/CUDA 等 | 运行设备 |

#### Lama Large (512px)

| 参数 | 类型 | 默认值 | 取值 | 说明 |
|------|------|--------|------|------|
| inpaint_size | 下拉框 | 1536 | 512 / 768 / 1024 / 1536 / 2048 | 修复分辨率，越多选项适应不同场景 |
| device | 下拉框 | (自动) | CPU/CUDA 等 | 运行设备 |
| precision | 下拉框 | bf16(CUDA) / fp32(其他) | fp32 / bf16 | 推理精度，bf16 更快但需支持 |

---

### 4. 翻译器 (Translator)

选择器下拉框可切换 **None**、**Sakura**、**LLM_API_Translator**。选择器下方有 **Source**(源语言) 和 **Target**(目标语言) 两个组合框，取值来自当前翻译器的支持列表。

#### None (透传)

不做翻译，直接返回原文。无参数。

#### LLM_API_Translator (通用多厂商 LLM 翻译器)

> 此为主要的 AI 翻译器，支持 OpenAI / Google / Grok / OpenRouter / LLM Studio / Ollama 等多个后端。
> 配置文件（API 密钥等）通过 **Manage Profiles** 按钮管理。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| Manage Profiles... | 按钮 | — | 打开配置文件管理对话框，可创建/编辑/删除 API 设置 |
| active_profile | 下拉框 | (空) | 快速切换已保存的配置文件 |
| max_requests_per_minute | 输入框(整数) | 20 | 每分钟每密钥最大请求数 |
| delay | 输入框(浮点) | 0.3 | 请求间隔（秒） |
| retry_attempts | 输入框(整数) | 3 | API 连接失败后的重试次数 |
| retry_timeout | 输入框(整数) | 15 | 重试等待时间（秒） |
| invalid_repeat_count | 输入框(整数) | 2 | 翻译数量不匹配时的重试次数 |
| proxy | 输入框 | (空) | 代理地址，`http(s)://user:password@host:port` |

#### Sakura (SakuraLLM)

> 针对日文视觉小说/漫画优化的本地 LLM 翻译器。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| low vram mode | 勾选框 | True | 低显存模式，单卡运行遇到 OOM 时开启 |
| api baseurl | 输入框 | `http://127.0.0.1:8080/v1` | Sakura API 地址 |
| dict path | 输入框 | (空) | 自定义词典路径，用于提高翻译质量 |
| version | 下拉框 | 0.9 | 模型版本：0.9 / 1.0 / galtransl-v1 |
| retry attempts | 输入框(整数) | 3 | 失败重试次数 |
| timeout | 输入框(整数) | 999 | 请求超时（秒） |
| max tokens | 输入框(整数) | 1024 | 最大输出 token 数 |
| repeat detect threshold | 输入框(整数) | 20 | 重复检测阈值 |
| force apply dict | 勾选框 | False | 强制应用词典（不确定含义时不要勾选） |
| do enlarge small kana | 勾选框 | False | 将小假名放大为正常大小 |

---

## 二、General 区域

### 1. 启动 (Startup)

| 显示名 | 类型 | 说明 |
|--------|------|------|
| Reopen last project on startup | 勾选框 | 启动时自动打开上次关闭时的项目 |

### 2. 排版 (Typesetting)

8 个排版属性的决定方式选择器，每个可选择 **decide by program**（由程序自动判断）或 **use global setting**（使用全局默认值）：

| 属性 | 对应设置 |
|------|---------|
| Font Size | 字号 |
| Stroke Size | 描边大小 |
| Font Color | 字体颜色 |
| Stroke Color | 描边颜色 |
| Effect | 文字效果 |
| Alignment | 对齐方式 |
| Writing-mode | 书写方向（横排/竖排） |
| Font Family | 字体（选项为：Keep existing / Always use global setting） |

额外选项：

| 显示名 | 类型 | 说明 |
|--------|------|------|
| Auto layout | 勾选框 | 自动调整文字布局 |
| To uppercase | 勾选框 | 将翻译结果转为大写 |
| Independent text styles for each projects | 勾选框 | 每个项目独立保存文字样式 |
| Exclude Fonts... | 按钮 | 打开字体排除对话框，可从列表中隐藏不需要的字体 |

### 3. 保存 (Save)

| 显示名 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| Result image format | 下拉框 | PNG | 导出结果图片格式：PNG / JPG / WEBP |
| Quality | 输入框(百分比) | 100 | 图片质量（仅对 JPG/WEBP 等有损格式有效） |
| Intermediate image format | 下拉框 | PNG | 管线中间过程的图像缓存格式：PNG |

### 4. 功能测试 (Feature Testing)

| 显示名 | 类型 | 默认值/范围 | 说明 |
|--------|------|-------------|------|
| Max Font Size (px) | 数字选择器 | 200 (10~1000) | 渲染时最大允许的字号（像素） |

### 5. 快捷键 (Keyboard Shortcuts)

> 每个操作可绑定多个快捷键。点击卡片上的 **+** 添加按键绑定，点击 **x** 移除，点击 **Reset** 恢复默认。

| 操作名 | 默认快捷键 |
|--------|-----------|
| Page Up | A |
| Page Down | D |
| Page Up (alt) | PgUp |
| Page Down (alt) | PgDown |
| Text Editor | T |
| Text Block | W |
| Draw Board | P |
| Zoom In | Ctrl++ |
| Zoom Out | Ctrl+- |
| Preview | Tab |
| Delete | Del |
| Delete (alt) | Ctrl+D |
| Select All | Ctrl+A |
| Bold | Ctrl+B |
| Italic | Ctrl+I |
| Underline | Ctrl+U |
| Undo | Ctrl+Z |
| Redo | Ctrl+Y |
| Page Search | Ctrl+F |
| Global Search | Ctrl+G |
| Escape | Escape |
| Inpaint | Space |
| Hand Tool | H |
| Rect Tool | R |
| Inpaint Tool | J |
| Pen Tool | B |
| Merge Tool | Ctrl+Shift+M |

---

## 三、附录：params 字典参考

> 以下供需要手动修改模块参数定义文件的开发者参考。

### 字典字段说明

```python
params = {
    "param_key": {                  # 参数键名，也是面板上的排序依据
        "value": 默认值,            # 默认值 —— 最常修改的字段
        "description": "说明文字",  # 鼠标悬停提示
        "display_name": "显示名",   # 面板上显示的名称（不设置则显示 key）
        "type": "selector",        # 控件类型（详见下表）
        "options": [选项列表],      # 下拉框的选项
    }
}
```

### 控件类型对照表

| `type` 值 | 面板上显示为 | 适用场景 |
|-----------|-------------|---------|
| `selector` | 下拉框 | 从有限选项中选择（如 device, model） |
| `checkbox` | 勾选框 | 开/关 |
| `check_group` | 水平排列的勾选框组 | 多选的互不排斥选项（如 YSG Label） |
| `line_editor` | 单行输入框 | 文本或数字输入 |
| `editor` | 多行文本框 | 较长的文本（如 prompt 模板） |
| `pushbtn` | 按钮 | 触发操作（如打开对话框） |
| (无 type 字段) | 单行输入框 | 简写形式的字符串/数字输入 |
