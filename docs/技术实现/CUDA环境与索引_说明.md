# CUDA 环境与索引选择说明

面向维护者。解释「装 CUDA 版 torch」这件事上哪些做法已经失效、脚本与 Python 侧
如何保持一致、以及出问题时怎么分层定位。

普通用户不需要读本文 —— 只跑 `install_cuda.bat`即可。

---

## 1. 为什么这件事值得单独立文档

`install_cuda.bat` 承担的是**唯一一条用户自救不了的路径**：装不上 GPU 版 torch 时，
用户既看不懂 pip 报错，也没法自己判断该选哪个索引。发版前必须保证：

- 选出来的索引**当前仍然可装**（索引会静默 EOL，不发公告）；
- 脚本与 Python 侧的映射**不打架**（曾经两边给同一张卡选了不同索引）；
- 失败时有**可读的降级路径**（manual 模式），而不是直接报错退出。

这三个都是「发生过事故」才补上的，下面逐条记录判据。

## 2. CUDA 档位映射（唯一真相）

映射的唯一依据是 **GPU 计算能力（compute capability, CC）**，不是显卡商品名。
商品名匹配在 50 系/40 系改名时就是错配的源头。

| CC 范围 | 索引 | CUDA | onnxruntime |
| --- | --- | --- | --- |
| ≥ 10（Blackwell 及更新） | `cu132` | 13.2 | `onnxruntime-gpu>=1.20,<1.29` |
| ≥ 9（Hopper/Ada） | `cu130` | 13.0 | 同上 |
| ≥ 6（Pascal 及更新） | `cu126` | 12.6 | 同上 |
| < 6 | 不支持 | — | — |

两处实现必须一致，且由回归台强制：

- 脚本侧：`install_cuda.bat` 里的 `GEQ <n>` → `set CUDA_INDEX=` 分支；
- Python 侧：`utils/env_diagnostic.py::pick_cuda_index`。

`tests/test_cuda_install_env.py::MappingConsistencyTests` 会逐个 CC 比对两侧结果。

**查询方式**：`nvidia-smi --query-gpu=compute_cap --format=csv,noheader`。
旧驱动不认这个 query 时回落到按型号猜（`utils/env_diagnostic.py::_cc_from_name`）。

## 3. 已失效 / 不该引用的索引

### `cu124` —— 已 EOL

实测（`scripts/check_cuda_env.py --indexes`）：`cu124` 最高只到 **torch 2.6.0**，
之后停止更新。而当前仓库基线是 torch 2.13.0。

**危害**：索引不会报错，只会**静默把 torch 降级**到 2.6.0。用户看到「装成功了」，
但版本反而变旧，随后出现一堆难以归因的兼容问题。这是本项目历史上最贵的一个坑。

**判据**：`install_cuda.bat` 不得出现 `set CUDA_INDEX=cu124`；
`tests/test_cuda_install_env.py::ScriptStaticTests` 静态检查钉着这一条。
面向用户的「别用 cu124」劝告文案则应当保留。

### `nightly/cu128` —— 不该作稳定源

早期记录中曾把此索引判为「已下线（实测为空）」。**该判断有误**，已更正：
实测 `https://download.pytorch.org/whl/nightly/cu128/torch/` 可访问，
提供 `torch 2.12.0`（nightly 构建）。

先前之所以读到「空」，是抓取时没解析到版本列表（抓到了非预期的页面），
并非索引真的不存在。**纠正记录**：它的正确问题是「nightly 是每晚构建通道，
版本会漂移」，不适合让普通用户照抄，**而不是**「装不上」。

CC ≥ 10 的稳定路径是 `cu132`，不需要 nightly。

## 4. onnxruntime 的双发行名问题

`onnxruntime`（CPU）与 `onnxruntime-gpu` 是**两个不同的发行包**，但：

- 共用同一个 `import onnxruntime` 名；
- 共用同一批 `capi/onnxruntime_providers_*.dll`；
- 各自留一份 `.dist-info`。

因此「用 GPU 版盖掉 CPU 版」时会留下两份 dist-info。此时
`pip uninstall onnxruntime` 找不到自己的元数据、拒绝执行，
DLL 永远清不干净 —— 表现为「装了 GPU 版但 `CUDAExecutionProvider` 还是不在列表里」。

**修法**：升级/替换前**两个发行名一起卸**：

```bat
-python -m pip uninstall onnxruntime onnxruntime-gpu -y
```

安装后再验一次 providers（`install_cuda.bat` 的 ORT_AFTER 分支）。

## 5. 脚本里几个必须保持的写法

这些都不是风格问题，每一条都对应一次静默失败：

| 写法 | 原因 |
| --- | --- |
| `for /f ... in (\`call "%PYTHON_EXE%" -c ...\`)` | 缺 `call` 时，带引号的路径被当成命令**名**；路径含空格（`D:\My Tools\`）就静默失败，CC 判成 0 → 表现为「有显卡却说检测不到」 |
| CC 守卫用 `^[0-9][0-9]*[0-9.]*$` | 旧写法 `^[0-9][0-9]*$` 拒绝 `12.0` 这种带小数点的输出，直接清成 0 |
| ORT 状态哨兵**预写**默认值 | `>` 重定向在**解析阶段**就建出空文件，所以 `if not exist` 永不触发；而 `set /p` 读空文件会留下**未定义**变量，既不等于 GOOD 也不等于 MISSING，分支走错 |
| 探测结果变量全部 `if not defined` 兜底 | `for /f` 无输出时变量保持上一次的值或未定义，必须显式归零 |

## 6. 三层验证台

`tests/test_cuda_install_env.py` 是回归台，分三层（文件头有说明）：

| 层 | 名称 | 联网 | 覆盖 |
| --- | --- | --- | --- |
| L1 | 静态检查 | 否 | 不该出现的写法、脚本↔Python 映射一致、README 不得推荐死索引 |
| L2 | 离网模拟 | 否 | 用 `tests/cuda_sandbox.py` 造假环境，把每条分支（各 CC 档、ORT 三种状态、降级保护、pip 失败）跑通 |
| L3 | 真环境 | 是 | `scripts/check_cuda_env.py`：索引存活 + 真解释器只读探测 |

跑法：

```bat
python -m unittest discover -s tests -p "test_cuda_install_env.py"
python scripts/check_cuda_env.py                    :: 索引 + 本机环境
python scripts/check_cuda_env.py --indexes          :: 只查索引
python scripts/check_cuda_env.py --python <python.exe>
```

`scripts/check_cuda_env.py` **只读**：不 install、不 uninstall、不写被检查的目录。

### L2 沙箱是怎么骗过脚本的

`tests/cuda_sandbox.py` 不真的装东西，做法是：把本机真解释器的 `python.exe` 与
依赖 DLL 拷进临时目录，配一个 `python3XX._pth` 把宿主解释器的 stdlib 路径告诉它，
再塞一个 `sitecustomize.py`。该 `sitecustomize` 在解释器启动时检查 `sys.orig_argv`，
凡是 `-c` 调用就按 `BT_*` 环境变量给出预设答案然后 `os._exit`。

几个必须注意的点：

- 必须用 **`sys.orig_argv`**，`sys.argv` 在 site 初始化阶段是空的；
- 必须用 **`os._exit`**，在 `sitecustomize` 里 `sys.exit` 会在 site 初始化途中
  抛 `SystemExit`，直接 `Fatal Python error: init_import_site`；
- `._pth` 的**文件名主干**必须与真实 DLL 一致（`python312.dll` → `python312._pth`），
  否则报 `No module named 'encodings'`；
- stdlib 路径**照抄宿主自己的 `._pth`**（`tests/cuda_sandbox.py::host_base_paths`），
  不要假定 `<home>/Lib`。宿主有两种布局：标准安装的 stdlib 在 `<home>/Lib`，
  而**嵌入式一键包**（`ballontrans_pylibs_win`）把它打在 `<home>/python3XX.zip`，
  `<home>/Lib` 里只有 `site-packages`。照搬 `<home>/Lib` 会让替身死在
  `Fatal Python error: init_fs_encoding`，而脚本那边只看到「探测回空」——
  表症是十几个 `'' != 'cu132'` 断言失败，看起来像脚本坏了。
- 由此 `Sandbox` 在启动替身后会**自检一次**：起不来就抛
  `tests/cuda_sandbox.py::SandboxUnavailable`，把宿主解释器、替身路径与解释器
  stderr 一并打出来。看到这个异常是**回归台缺零件**，不要去改 `install_cuda.bat`。

## 7. 出问题时的分诊顺序

1. **先跑 L1**：几秒钟，不联网。大多数「改坏了」在这一层就暴露。
2. **再跑 L2**：确认分支逻辑没被改错（尤其 CC 判 0、ORT 分支错走）。
3. **最后跑 L3**：确认索引还活着、目标解释器状态与预期一致。

真机排障时，`scripts/check_cuda_env.py --python <用户的一键包>/python.exe`
可以一次性给出「这个用户的 torch/onnxruntime/CC 是什么、脚本会怎么判、会选哪个索引」。
