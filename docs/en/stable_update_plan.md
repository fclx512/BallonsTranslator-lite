# Stable Release Update Plan

Applicable after the v1.1+ official release, targeting end users without programming experience.

## 1. Distribution Method: PyInstaller Packaging

### Package as a folder (recommended `--onedir`)

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

- `--onedir` generates a folder (not a single-file exe), faster startup, convenient for incremental updates
- Requires bundling PyTorch and its dependencies
- The project already has prior experience with the `ballontrans_pylibs_win` portable environment, which can be referenced

### Release on GitHub Releases

For each release:
1. Update `VERSION` in `launch.py` (e.g., `1.1.0`)
2. Tag: `git tag v1.1.0 && git push origin v1.1.0`
3. GitHub Actions automatically packages and uploads to Releases (write a CI workflow)
4. Write changelog in the Release body

## 2. Update Check: GitHub API (without git dependency)

### Check Logic

```
GET https://api.github.com/repos/fclx512/BallonsTranslator/releases/latest
Headers: User-Agent: BallonsTranslator-lite/<version>
         Accept: application/vnd.github+json
```

- Parse `tag_name` from the response (e.g., `v1.1.0`)
- Compare with local `VERSION` using semantic version comparison
- `tag_name` is greater → new version available
- Display `body` (Markdown) as the changelog

### Environment Adaptation

| Runtime Environment | Check Update | Execute Update |
|---------|---------|---------|
| Dev environment with git | `git rev-parse` compare commits | `git pull` |
| No git / PyInstaller packaged | GitHub API compare version numbers | Download new package and replace (see Section 3) |

### Error Handling

| Scenario | Handling |
|------|------|
| No network | "Unable to connect to GitHub, please check your network" |
| API rate limit (60/hour) | Show remaining wait time (parse `X-RateLimit-Reset` header) |
| No Releases (404) | "No release version information available" |
| JSON parse failure | "Server returned an error, please try again later" |

## 3. Update Execution

### Option A: Browser Redirect (lowest implementation cost)

Detect new version → Show prompt → User clicks → `webbrowser.open(release.html_url)` → User manually downloads and installs.

- Pros: Simple implementation
- Cons: Requires manual user action

### Option B: Automatic Download + Replacement Script (recommended)

```
Main program downloads release zip
  → Extract to temp directory {tmp}
  → Write update script {tmp}/update.bat
  → Launch update.bat, main program exits

update.bat:
  1. Wait for main process to exit (timeout /t 3)
  2. xcopy {tmp}/* {app_dir}/ /E /Y
  3. Launch new version main program
  4. Delete temp directory
```

- Fully transparent to the user, one-click update
- `update.bat` must perform file replacement after the main program exits (Windows does not allow overwriting a running executable)

### Option C: Standalone updater.exe

Similar to VS Code/Chrome approach: the main program does not handle updates; a small standalone updater process handles downloading and replacement. More suitable for large projects, over-engineered here.

## 4. File Reference Table

Files planned for modification:

| File | Change |
|------|------|
| `ui/update_checker.py` | `UpdateThread` changed to HTTP API + git dual mode; `AboutDialog` adapted to new version text |
| `utils/update_cache.py` | Cache key changed from commit hash to version string |
| `launch.py` | `--update` switched to GitHub API |
| `ui/mainwindow.py` | Remove git parameters passed to AboutDialog |
| `translate/zh_CN.ts` | Update translation strings |
| `.github/workflows/release.yml` | New CI: packaging + release publishing |

## 5. Implementation Timeline

- When the version number moves beyond the `1.0.0` placeholder and formal semantic version management begins
- After determining the feature scope for the first stable release
- After the packaging process has been tested and verified
