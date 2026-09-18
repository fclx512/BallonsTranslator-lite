# scripts/probes — 真机探针（"测出来的"结论的可复算工具）

这里放的是**一次性排查用过、但结论需要长期复算**的探针脚本：内存归因与释放阶梯、
区域再检测的真机验收。它们不是日常入口，跑之前先读对应的实现文档。

## 前提（三个都容易踩）

1. **必须用便携解释器**：`./ballontrans_pylibs_win/python.exe`。系统 Python 与
   WorkBuddy 托管的那份都缺依赖（cv2／onnxruntime／torch），跑不起来。
2. **脚本里有本机路径常量**（`APP` = 仓库绝对路径、`SAMPLE`／`PROJ` = 样本项目目录）。
   换机器先把这些常量改掉——本目录刻意保持"当时实测的形态"，不做通用化改造，以免
   改坏了再用时对不上文档里的数字。
3. **有真机依赖**：GPU 相关探针要在有 CUDA 的机器上跑；验收类探针要 `data/models/`
   下的检测／识别模型 + 真实项目目录。多数脚本会往 `tmp/` 写输出（`tmp/` 不入库）。

样本目录约定为**只读**（`D:\汉化\施工区`）；需要写操作的实验一律在副本
（`D:\汉化\施工区副本`）上做。探针本身不写项目数据。

## 一、内存归因与释放（文档：`docs/技术实现/内存释放_设计与实现.md`、`区域再检测_设计与实现.md`）

| 脚本 | 做什么 | 关键数字 / 结论 |
|---|---|---|
| `mem_attribution.py` | 区域再检测一次下来，内存长在哪一步、卸得掉吗 | 元凶＝给这条路径新建的 ORT **CUDA 会话**：一次 `detect` +835MB，`unload_model` 只回收 50MB |
| `mem_ort_session_config.py` | ORT 会话配置对照（cudnn 搜索模式 × CPU arena × arena 策略 ＋ CPU 对照）；**每档必须在独立子进程里测** | 三个开关只把峰值 835 → 730，量级不变 ⇒ 事后清缓存救不回来 |
| `mem_device_speed.py` | CPU vs CUDA 的**耗时**对照（会话创建 + 单次小裁剪推理） | 单次推理 CPU 19~93ms vs CUDA 7~21ms，一次手势只差约 20ms；会话重建 0.17s |
| `mem_device_speed_v1.py` | 同上的初版（保留备查，**以 `mem_device_speed.py` 为准**） | — |
| `mem_after_cpu_default.py` | 改成"CPU 检测器 + 用完即卸"之后的复测（走生产路径） | 峰值 **+148MB**、卸完相对基线 +47MB、单次手势 0.21~0.32s |
| `release_ladder.py` | 释放阶梯：卸模型 → `empty_cache` → `cudaDeviceReset` → `EmptyWorkingSet` | 1503 → 1503 → 1503 → **1295** → **111**（reset 才是真还，EmptyWorkingSet 是"交回"） |
| `release_module_attribution.py` | 那 ~690MB 是堆还是驱动/cuDNN DLL 的文件映射（决定进程内能不能救） | 模块工作集 350 → 407MB（`cublasLt64_13.dll` 单项 144MB） |
| `release_after_trim.py` | 交回工作集之后再跑一次 CUDA：还能不能用、多慢、会不会涨回来 | 回升到 420MB，首次 0.33s |
| `release_torch_after_reset.py` | **安全性质疑**：reset 前后 torch（caching allocator / cuBLAS）还能不能用 | 先 `empty_cache()` 再 reset 正常；**不先清就 reset → `CUBLAS_STATUS_INTERNAL_ERROR` + illegal access**（这条是硬约束） |

对应实现：`utils/memory_release.py`（三步编排）+ 设置页 Models → Management 的
「释放内存」按钮；`pcfg.region_redetect_device` 默认 `cpu` 就是上面第一条的产物。

## 二、区域再检测真机验收（文档：`docs/技术实现/区域再检测_设计与实现.md` §6）

| 脚本 | 做什么 | 期望值 |
|---|---|---|
| `region_redetect_accept.py` | 在工作副本上跑 `RegionRedetect.plan()`（只读），核对三处拉框的检出 | 063.jpg (350,265)-(690,435) → **5~6 块且全部倾斜**；047.jpeg (560,1200)-(900,1325) → **3 块**；047.jpeg (140,340)-(440,445) → **2 块** |
| `region_redetect_replace_probe.py` | 同一裁剪里 ysgyolo 检出多少、待替换的既有块是什么 | 同区域 ysgyolo **0 块**（复现"ysgyolo 全漏、ppocrv6 补上"的前提）；替换打的是重复/大框，不误伤正常块 |

落点判据的回归台不在本目录，它是常驻入口：`scripts/region_redetect_order.py`。

## 三、泛用工作台的参数复算

不在本目录——已通用化为常驻入口 `scripts/workbench_recalc.py`
（`merge`／`c1`／`expand`／`queue`／`review`／`hook`／`list`，只读、带 `--project`）。
那份是长期要反复跑的（阈值与默认值靠实战调整），所以做成了带命令行参数的正式脚本，
而不是这里的一次性探针。
