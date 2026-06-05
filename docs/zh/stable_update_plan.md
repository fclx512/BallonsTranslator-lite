# 稳定版更新方案

适用于 v1.1+ 正式版发布后，面向无编程经验的最终用户。

## 1. 分发方式：PyInstaller 打包

### 打包为文件夹发布（推荐 `--onedir`）

```bash
pyinstaller --onedir \
  --name BallonsTranslator-lite \
  --add-data "modules;modules" \
  --add-data "config;config" \
  --add-data "ui/qml;ui/qml" \
  --add-data "translate;translate" \
  --hidden-import qtpy \
  --hidden-import PyQt6 \
  launch.py
```

- `--onedir` 生成文件夹（非单文件 exe），启动更快，方便增量更新
- 需要打包 PyTorch 及其依赖
- 项目已有 `ballontrans_pylibs_win` portable 环境的先例，可参考

### 发布到 GitHub Releases

每次发版时：
1. 更新 `launch.py` 中的 `VERSION`（如 `1.1.0`）
2. 打 tag：`git tag v1.1.0 && git push origin v1.1.0`
3. GitHub Actions 自动打包并上传到 Releases（编写 CI workflow）
4. Release body 写更新日志

## 2. 更新检查：GitHub API（不依赖 git）

### 检查逻辑

```
GET https://api.github.com/repos/fclx512/BallonsTranslator/releases/latest
Headers: User-Agent: BallonsTranslator-lite/<version>
         Accept: application/vnd.github+json
```

- 解析响应中的 `tag_name`（如 `v1.1.0`）
- 与本地 `VERSION` 做语义版本比较
- `tag_name` 更大 → 有新版本
- 显示 `body`（Markdown）作为更新日志

### 环境适配

| 运行环境 | 检查更新 | 执行更新 |
|---------|---------|---------|
| 有 git 的开发环境 | `git rev-parse` 对比 commit | `git pull` |
| 无 git / PyInstaller 打包 | GitHub API 对比版本号 | 下载新包替换（见第 3 节） |

### 错误处理

| 场景 | 处理 |
|------|------|
| 无网络 | "无法连接 GitHub，请检查网络" |
| API 限流（60次/小时） | 显示剩余等待时间（解析 `X-RateLimit-Reset` 头） |
| 无 Releases（404） | "暂无发布版本信息" |
| JSON 解析失败 | "服务器返回异常，请稍后重试" |

## 3. 更新执行

### 方案 A：浏览器跳转（最低实现成本）

检测到新版本 → 弹出提示 → 用户点击 → `webbrowser.open(release.html_url)` → 用户手动下载安装。

- 优点：实现简单
- 缺点：需要用户手动操作

### 方案 B：自动下载 + 替换脚本（推荐）

```
主程序下载 release zip
  → 解压到临时目录 {tmp}
  → 写入更新脚本 {tmp}/update.bat
  → 启动 update.bat，主程序退出

update.bat:
  1. 等待主进程退出（timeout /t 3）
  2. xcopy {tmp}/* {app_dir}/ /E /Y
  3. 启动新版本主程序
  4. 删除临时目录
```

- 对用户完全透明，一键更新
- `update.bat` 需要在主程序退出后执行文件替换（Windows 不允许覆盖正在运行的可执行文件）

### 方案 C：独立 updater.exe

类似 VS Code/Chrome 的做法：主程序不负责更新，由一个小型独立 updater 进程负责下载和替换。对大型项目更合适，这里略过重。

## 4. 文件对照表

计划修改的文件：

| 文件 | 改动 |
|------|------|
| `ui/update_checker.py` | `UpdateThread` 改为 HTTP API + git 双模式；`AboutDialog` 适配新版本文本 |
| `utils/update_cache.py` | 缓存 key 从 commit hash 改为 version string |
| `launch.py` | `--update` 改用 GitHub API |
| `ui/mainwindow.py` | 移除传递给 AboutDialog 的 git 参数 |
| `translate/zh_CN.ts` | 更新翻译字符串 |
| `.github/workflows/release.yml` | 新增 CI：打包 + 发布 Release |

## 5. 实施时机

- 版本号脱离 `1.0.0` 占位，开始正式语义版本号管理时
- 确定首次稳定版功能范围后
- 打包流程经测试无误后
