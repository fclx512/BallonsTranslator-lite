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
| `release_ladder.py` | 释放阶梯：卸模型 → `empty_cache` → `cudaDeviceReset` → `EmptyWorkingSet` | 1503 → 1503 → 1503 → **1295** → **111**（reset 才是真还，EmptyWorkingSet 是"交回"）。**reset 那一步只作历史证据**：它的后果见下面 `release_cuda_context_aftermath.py`，能力已从代码里删除 |
| `release_module_attribution.py` | 那 ~690MB 是堆还是驱动/cuDNN DLL 的文件映射（决定进程内能不能救） | 模块工作集 350 → 407MB（`cublasLt64_13.dll` 单项 144MB） |
| `release_after_trim.py` | 交回工作集之后再跑一次 CUDA：还能不能用、多慢、会不会涨回来 | 回升到 420MB，首次 0.33s |
| `release_torch_after_reset.py` | **安全性质疑**：reset 前后 torch（caching allocator / cuBLAS）还能不能用 | 先 `empty_cache()` 再 reset 正常；**不先清就 reset → `CUBLAS_STATUS_INTERNAL_ERROR` + illegal access**（这条是硬约束） |
| `release_cuda_context_aftermath.py` | 两件事：①验收现在的「释放内存」（卸载 + 交回工作集）之后 torch 与 `paddleocr_vl_manga` 是否照常；②复算**被删掉**的 `cudaDeviceReset` 那一步的后果（本地 ctypes 直调，不走应用代码） | ①工作集 832 → 4.5MB，torch 分配/matmul 照常、VL 加载 4.2s + 识别照常；②reset 之后 `torch.cuda.is_available()` **仍返回 True** 而第一次分配报 `CUDA error: invalid argument`，另一次实测直接段错误（exit `0xC0000005`）⇒ 只多还 70~166MB 却要重启应用、还可能崩进程，**该能力已从代码里删除**（用户 2026-09-20 拍板）。原委见 `docs/技术实现/内存释放_设计与实现.md` §4；探针第 2 步可能打崩进程，故放最后 |

对应实现：`utils/memory_release.py`（两步编排：卸模型 → 交回工作集）+ 设置页
Models → Management 的「释放内存」按钮；`pcfg.region_redetect_device` 默认 `cpu`
就是上面第一条的产物。

## 二、区域再检测真机验收（文档：`docs/技术实现/区域再检测_设计与实现.md` §6）

| 脚本 | 做什么 | 期望值 |
|---|---|---|
| `region_redetect_accept.py` | 在工作副本上跑 `RegionRedetect.plan()`（只读），核对三处拉框的检出 | 063.jpg (350,265)-(690,435) → **5~6 块且全部倾斜**；047.jpeg (560,1200)-(900,1325) → **3 块**；047.jpeg (140,340)-(440,445) → **2 块** |
| `region_redetect_replace_probe.py` | 同一裁剪里 ysgyolo 检出多少、待替换的既有块是什么 | 同区域 ysgyolo **0 块**（复现"ysgyolo 全漏、ppocrv6 补上"的前提）；替换打的是重复/大框，不误伤正常块 |

落点判据的回归台不在本目录，它是常驻入口：`scripts/region_redetect_order.py`。

## 二点五、框扩张 → 译文居中实测（2026-09-19）

| 脚本 | 做什么 | 结论 |
|---|---|---|
| `expand_centering_probe.py` | 把 `projects/004_819b9e93` 拷到 `tmp/`，真机拉起主窗口，全书填占位译文，扩张前后各渲染一遍结果图，逐块同窗口并排裁剪到 `tmp/expand_centering/crop/` | 扩张后文本**确实按新框重排**（能多排字/换行）；「是否居中」取决于块自身 `fontformat.alignment`（居中＝新框内重排居中，左＝锚定左上）——对齐是块属性，不是扩张页能决定的。该工程 37 块默认全是左对齐 |

## 三、泛用工作台的参数复算

不在本目录——已通用化为常驻入口 `scripts/workbench_recalc.py`
（`merge`／`c1`／`expand`／`queue`／`review`／`hook`／`list`，只读、带 `--project`）。
那份是长期要反复跑的（阈值与默认值靠实战调整），所以做成了带命令行参数的正式脚本，
而不是这里的一次性探针。

## 四、paddleocr_vl_manga 接入验收（2026-09-20）

文档：`docs/技术实现/paddle-ocr-for-manga_接入调研.md`（§3 的性能数字出自这里）、
`docs/技术实现/模型文件管理_测试流程.md` 第 4 层。两个都**只读、不联网**（模型已在本地）。

| 脚本 | 做什么 | 关键数字 / 结论 |
|---|---|---|
| `ocr_vl_tokenizer.py` | 核对 tokenizer 的两条加载路径：「吃仓库自带 tokenizer.json」（`add_prefix_space=None`）与「装 sentencepiece 走 slow→fast 转换」（作者训练时的实际路径） | 两者 **token id 逐条一致**（17/17 样本、词表 101314 全同、chat 模板 token 相同）⇒ 用 A 路即可，**不必引入 sentencepiece**。缺这个 None 参数时缺 sentencepiece 就直接抛 `Cannot instantiate this tokenizer from a slow version`（＝用户遇到的「OCR 失败」）。权重更新后重跑 |
| `ocr_vl_manga_accept.py` | 用施工区副本里块数最多的一页跑逐块识别（与项目内既有 ppocrv6 文本并排 + 相似度）、再试一次整页一次喂，输出耗时与峰值显存；报告写 `tmp/vl_e2e_report.md` | 加载 3.8s；加载后 allocated 1.80GB；**峰值 allocated 2.51GB / reserved 3.22GB**（估的「3GB 上下」无误）；单块中位 **192ms**（首块 0.9s 预热）⇒ 约 6s/页，比原文「慢两个数量级」的估算乐观。整页一次喂**不可用**：同页 27 块只出 11 行且无坐标 ⇒ 检测必须自己出 |

第二条的样本目录常量 `PROJ_DIR` 指向 `D:\汉化\施工区副本`（换机器要改）。
报告是 UTF-8，**别让脚本输出走 PowerShell 管道**——控制台是 GBK，非 ASCII 会乱码。
