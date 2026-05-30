# Shortcuts (快捷键) System Guide

## Architecture

快捷键系统由三层构成：

```
DEFAULT_SHORTCUTS (configpanel.py:284)     ← 默认值定义
        ↓
pcfg.shortcuts (config.json)               ← 用户自定义覆盖
        ↓
MainWindow._install_shortcuts() (mainwindow.py)  ← QShortcut 注册
```

查询优先级：`pcfg.shortcuts` > `DEFAULT_SHORTCUTS`

## 添加新快捷键的步骤

### 1. 在 DEFAULT_SHORTCUTS 中定义默认键

文件：[ui/configpanel.py](ui/configpanel.py) 第 284 行

```python
DEFAULT_SHORTCUTS = {
    'my_action': ['Ctrl+Shift+X'],
    # ...
}
```

键名（action_id）使用 snake_case。值是一个 list，可绑定多个键序列。

### 2. 在 _ACTION_NAMES 中添加显示名称

```python
_ACTION_NAMES = {
    'my_action': 'My Action',
    # ...
}
```

### 3. 将动作分组到 _SHORTCUT_GROUPS

```python
_SHORTCUT_GROUPS = [
    ('GroupName', ['my_action', ...]),
    # ...
]
```

### 4. 注册 QShortcut

**唯一正确的位置：[ui/mainwindow.py](ui/mainwindow.py) 的 `_install_shortcuts()` 方法内**

```python
def _install_shortcuts(self):
    # ...
    self.shortcut_registry['my_action'] = self._make_shortcuts(
        'my_action',            # action_id — 必须与 DEFAULT_SHORTCUTS 的 key 一致
        ['Ctrl+Shift+X'],       # defaults  — 必须与 DEFAULT_SHORTCUTS 的值一致
        self.my_handler_method, # slot     — MainWindow 上的处理方法
    )
```

`_make_shortcuts()` 会自动从 `pcfg.shortcuts` 读取用户自定义值，有则用自定义，无则 fallback 到第二个参数指定的默认值。

### 5. 编写 handler 方法

handler 是 MainWindow 上的普通方法，不需要判断 sender：

```python
def my_handler_method(self):
    if self._is_canvas_mode():  # 仅在画布模式下响应
        # ... 具体逻辑 ...
```

如果需要在 handler 中识别是按了哪个键触发的，用 `self.sender()`：

```python
def shortcutBefore(self):
    sender = self.sender()
    if isinstance(sender, QShortcut) and sender.key() == QKEY.Key_A:
        if self.canvas.editing_textblkitem is not None:
            return
    # ... 实际逻辑 ...
```

## 必须遵守的规则

### 规则 1：所有快捷键必须在 `_install_shortcuts()` 中注册

❌ 禁止在 `TitleBar`、`LeftBar` 或其他地方调用 `QAction.setShortcut()`。  
✅ 所有键盘快捷键统一经 `_install_shortcuts()` → `_make_shortcuts()` 创建 `QShortcut`。

理由：硬编码的 `setShortcut()` 无法被快捷键编辑器覆盖，用户在界面中修改后不会生效。

### 规则 2：两个参数必须一致

`_make_shortcuts(action_id, defaults, slot)` 的 `action_id` 和 `defaults` 必须与 `DEFAULT_SHORTCUTS` 中的定义完全一致：

```python
# DEFAULT_SHORTCUTS = {'my_action': ['Ctrl+Shift+X']}

# ✅ 正确
self._make_shortcuts('my_action', ['Ctrl+Shift+X'], self.handler)

# ❌ 错误 — action_id 不一致
self._make_shortcuts('my_action_xxx', ['Ctrl+Shift+X'], self.handler)

# ❌ 错误 — defaults 不一致（用户 reset 后会得到不同的默认值）
self._make_shortcuts('my_action', ['Ctrl+Alt+Y'], self.handler)
```

### 规则 3：用 `QShortcut` 而非 `QAction.setShortcut()`

- `QShortcut` 直接注册在 `MainWindow` 上，可通过 `refreshShortcuts()` 热更新
- `QAction.setShortcut()` 注册在父 widget 上，无法通过配置系统管理

### 规则 4：菜单/工具栏中的快捷键提示

菜单中的 `QAction` 不应该调用 `setShortcut()`（快捷键已在 `_install_shortcuts()` 中注册）。菜单项不会自动显示快捷键提示——目前的设计是让用户在快捷键编辑器中查看。

## 热更新机制

`ShortcutDialog` 关闭后触发链：

```
ShortcutDialog.close()
  → ConfigPanel.shortcuts_changed Signal
  → MainWindow.refreshShortcuts()
  → 删除所有旧 QShortcut
  → 调用 _install_shortcuts() 用最新 pcfg.shortcuts 重建
```

修改快捷键后立即生效，无需重启。

## 文件索引

| 文件 | 职责 |
|------|------|
| [ui/configpanel.py](ui/configpanel.py) | `DEFAULT_SHORTCUTS` / `_ACTION_NAMES` / `_SHORTCUT_GROUPS` 定义、快捷键编辑 UI |
| [ui/mainwindow.py](ui/mainwindow.py) | `_install_shortcuts()` 注册、`refreshShortcuts()` 热更新、handler 方法 |
| [utils/config.py](utils/config.py) | `ProgramConfig.shortcuts` 字段、`save_config()` 持久化 |
