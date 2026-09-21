# paddle-ocr-for-manga 接入调研

> 状态：已完结归档（2026-09-19）——本篇为压缩存档，只留结论与指针；实施过程见 git 历史。

> 调研 2026-09-19（撰写时本机无 CUDA、性能数字为估算或官方数据），2026-09-20 端到端跑通后回填实测并逐处更正；原文中已被更正覆盖的估算叙述已删。模块已落地为 `modules/ocr/ocr_vl_manga.py`；模型文件侧的机制与复算见 `docs/技术实现/模型文件管理_设计方案_存档.md`、`docs/技术实现/模型文件管理_测试流程.md`（第 4 层）。

## 0. 结论先行

- 该需求指 **jzhang533 的 PaddleOCR-VL-For-Manga**（GitHub 训练仓库 + HuggingFace 权重，见 §8）。若指的是另一个同名项目，本文需按其仓库重写。
- 最关键事实：官方训练仓库的推理示例与 HF demo 都直接用 **HuggingFace transformers（PyTorch）加载**，权重仓库对 transformers 自包含——**整条路线不需要安装 paddlepaddle，也不需要安装系统级 CUDA Toolkit / cuDNN**，只需要 CUDA 版 torch 轮子（轮子自带 CUDA 运行时，仅要求 NVIDIA 驱动）。名字里的 "Paddle" 只是血统。
- 对接方式：仿现有识别-only 模块新增一个 OCR 模块，**继续用本项目自己的检测器**（官方明确说漫画场景不要配其版面模型），只换识别器。
- 定位：日文漫画专精的「质量优先」OCR，逐块自回归解码；**不建议作默认识别器**。

## 1. 模型基本情况

| 项 | 内容 |
| --- | --- |
| 作者/基座 | jzhang533（PaddlePaddle 联合创始人）；PaddlePaddle/PaddleOCR-VL（0.9B 视觉语言 OCR 模型，官方宣称支持 109 语种） |
| 微调数据 | Manga109-s 文本区域 crop 约 10 万（90% 训练 / 10% 测试）+ 150 万合成样本 |
| 精度 | Manga109-s 测试集整句准确率 **70%**（基座同测仅 27%） |
| 任务形态 | 输入单个文本区域 crop（非整页）+ prompt `OCR:` → 输出多行日文文本（换行分行） |
| 许可 | Apache 2.0（权重仓库声明） |
| 语言范围 | 日文漫画专精；其他语种/通用文档不适用 |

架构（读自 HF config.json）：SigLIP NaViT 视觉编码器（27 层、hidden 1152、patch 14、动态分辨率，min/max_pixels = 147384 / 2822400）+ 18 层自回归解码器（hidden 1024、GQA 16 注意力头 / 2 KV 头、词表 103424），合计约 0.9B 参数；权重为**单文件** safetensors 约 1.92GB（bf16）。HF 权重仓库自包含（config + auto_map 自定义代码 4 个 py + tokenizer + chat template + processor 配置，共 15 个文件），`trust_remote_code=True` 指向本地目录即可加载，不需联网、不需基座仓库。

**它不是「检测 + 识别二合一」，只做识别。** 权重仓库 README 把版面分析交给 PP-DocLayoutV2，并特意注明「漫画的阅读顺序与版面很不一样」；作者教程写明 Best Use Case 是 manga text crops（individual text bubbles/regions, not full pages）。**整页一次喂实测不可用**：块数最多的一页 27 块只出 11 行、且**输出不带任何坐标**——即便认对一部分也无从把文本填回各自的框。所以**检测必须由本项目自己的检测器提供**（§4 的对接方案即按此前提：只换识别器）。

## 2. 推理契约与关键参数

调用形态（官方示例与 demo 一致）：crop 转 RGB 后与 prompt `OCR:` 组成 chat message，`processor.apply_chat_template` + `processor(...)` 出张量，`model.generate` 贪心解码，`batch_decode` 取新增 token 即识别文本。

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| dtype | bfloat16（Ampere 及以后）；老卡改 float16 | 权重即 bf16 |
| attn_implementation | sdpa（默认即可，显式传更稳） | 远端代码声明支持 sdpa；flash-attn 2 为可选加速，代码里 `is_flash_attn_2_available()` 不满足时自动跳过——**Windows 不装 flash-attn** |
| use_cache | **必须显式传 True** | config 与 generation_config 都写死 `use_cache: false`（训练侧习惯），不覆盖则自回归解码不走 KV cache、慢数倍；官方两份推理代码均显式传了 True |
| max_new_tokens | 256 | 气泡 crop 足够；密集旁白可调大（demo 用 2048）。做成模块参数 |
| min/max_pixels | 147384 / 2822400 | 每 28×28px 合并为 1 个视觉 token（patch 14 × spatial merge 2），即单 crop 约 188～3600 视觉 token；可下调 max_pixels 换速度 |

识别结果**无置信度分数**（生成式模型只有 token 概率）；输出保留模型原样，包括官方自述的典型误差类型：全角/半角混排（`！？` vs `!?`、`ＯＫ` vs `ok`）与 CJK 部首变体字（`⼈` U+2F08 vs `人` U+4EBA）。

## 3. 依赖与资源开销

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| torch | **CUDA 12.x 构建** | 轮子自带 CUDA 运行时 DLL，只要求 NVIDIA 驱动；本项目 onnxruntime-gpu 路径本就借 torch lib 目录的 CUDA 12 DLL（见 `modules/ocr/ocr_onnx.py::PaddleOCRv6ONNX` 的 `_ensure_cuda_dll_path`），主力机若已在用 GPU 检测/修复即说明 torch 已是 CUDA 版，**无需再动**。2026-09-20 实测：主力机 torch `2.13.0+cu132`、`cuda.is_available()` 为真、GPU 为 RTX 5070 Ti Laptop（11.9GB、CC 12.0、原生支持 bf16），已满足 |
| torchvision | 与 torch 同源同版 | 仅当主力机在用 ysgyolo/ultralytics 检测器时需要，重装 torch 时必须连带 |
| transformers | >=4.57.1, <5 | config 声明 transformers_version 4.57.1；远端代码用到 GradientCheckpointingLayer、check_model_inputs 等 4.56+ API。安装连带 tokenizers / safetensors / huggingface-hub |
| accelerate | 仅 `device_map="auto"` 需要 | 用 `.to(device)` 加载则不需要 |
| sentencepiece | **不需要** | 仓库 `tokenizer_config.json` 写的是 `tokenizer_class: LlamaTokenizer` + `add_prefix_space: false`，该组合会让 transformers 的 `LlamaTokenizerFast` 覆写 `from_slow=True`、绕开仓库自带的 `tokenizer.json` 去构造 slow 版；缺 sentencepiece 即报 `Cannot instantiate this tokenizer from a slow version`（**用户跑管线时遇到的「OCR 失败」就是这个**）。而 `tokenizer.json` 的 `pre_tokenizer` 为空、两条路径 token id 逐条一致，故正确做法是加载时显式传 `add_prefix_space=None` 抑制 `from_slow`。复算：`scripts/probes/ocr_vl_tokenizer.py`。**flash-attn 不装**（见 §2） |

显存与速度（**已实测** 2026-09-20，RTX 5070 Ti Laptop 11.9GB / torch 2.13.0+cu132 / bf16 / sdpa，`scripts/probes/ocr_vl_manga_accept.py`）：

| 项 | 实测 |
| --- | --- |
| 加载耗时 | **3.8s**（本地目录、无联网） |
| 加载后显存 | allocated **1.80GB** / reserved 1.83GB |
| 峰值显存 | allocated **2.51GB** / reserved 3.22GB（12 块 + 一次整页） |
| 单块耗时 | 中位 **192ms**，最快 86ms，最慢 310ms；首块约 0.9s（预热） |
| 一页估算 | 该页 27 块 ⇒ **约 6s/页**（0.9s 预热 + 26 × 0.19s） |

两处估算的实测修正：早期「峰值 3GB 上下」的估算与实际相符（2.5GB）；**「比 onnx 系慢约两个数量级」的估算偏悲观**——实测每块不到 0.2s，与 onnx 系同页差距在一个数量级以内，小气泡占多数的页面甚至更快。一个反直觉的耗因：processor 会把小 crop **放大到 `min_pixels = 147384`（≈384×384）** 再编码，所以几十像素的拟声词小框并不比整气泡便宜多少。

CPU 推理仍不实用，本模块按 GPU-only 对待，device 缺省即 cuda。

## 4. 模块对接与落地形态（已实现）

落地为 `modules/ocr/ocr_vl_manga.py::PaddleOCRVLManga`，注册名 `paddleocr_vl_manga`，继承 `modules/ocr/base.py::OCRBase`，形态参照 `modules/ocr/ocr_onnx.py::PaddleOCRv6ONNX`。

- **整块裁剪，不逐行**：VL 模型在 Manga109-s 的整块文本区域 crop 上训练，块内多行/多列一起喂才能借到模型自己的阅读顺序。取块内所有 lines 点集的轴对齐外接范围（`utils/block_geometry.py::poly_bands`，口径同区域再检测）直接切片，**不做**透视校正与重采样（训练 crop 本就是轴对齐外接框，切片省掉网点插值失真）。**不用** `utils/textblock.py::TextBlock` 的 `get_transformed_region`——那是渲染用的，会把竖排旋转成横排；竖排 crop 必须保持原始朝向。也**没用** `onnxocr.utils.get_rotate_crop_image`：本模块只声明 `transformers`，引入 onnxocr 会多一个未声明的依赖。
- **批量**：整页所有块拼一个 batch 一次 generate，`processor.tokenizer.padding_side = "left"`（生成式批量推理标配）。**批量左 padding 与该模型 mrope 位置编码的兼容性未验证**，故界面上 `batch_size` 缺省 1（逐块）；要试批量须显式调大并逐块比对结果。
- **输出与置信度**：按换行 split 后写入 `blk.text`（List[str]），与现有 OCR 契约一致；`ocr_img` 整图模式照 ocr_onnx 先例拒绝实现。生成式无分数，`utils/block_tags.py::apply_ocr_confidence_tag` 无从挂起——「OCR 置信度低」自动标签在本模块下不生效（后续可用输出 token 平均 logprob 造伪分数，另行评估）。
- **依赖安装与权重**：`requires_packages = ["transformers>=4.57.1,<5"]`；**选中模块即起后台任务**安装/下载（`background_download_only`），不走加载期同步（1.9GB 同步下会冻住界面）。缺文件抛 `modules/base.py::MissingModelFilesError`，由 UI 指路到设置页 Models →「模型文件」。镜像**已内建**（`utils/download_util.py::download_url_to_file` 会走 `utils/mirror.py::maybe_mirror_url`，设置页也有「网络与镜像设置」一键国内镜像）；手工放置入口＝设置页 Models →「模型文件」的「打开目录」＋期望清单。机制与验收见 `docs/技术实现/模型文件管理_设计方案_存档.md`。
- **机制复用**：device 参数用 `modules/base.py::DEVICE_SELECTOR`；`_resolve_device` 额外兜了「配置里存着 cuda 但当前环境不可用」——降级到 CPU 并告警，而不是直接抛；params 的 description 自动进 i18n 提取链（`scripts/i18n_common.py::extract_param_descriptions`）。
- **UI 定位**：与 paddleocr_v6_onnx（通用、快）、mit 系并列的日文漫画质量优先选项；默认检测器、默认 OCR 均不变。

## 5. 上手与验收

前置：NVIDIA 驱动足够新（CUDA 12.x 轮子一般要求 ≥ 550 一档）；**不需要**安装 CUDA Toolkit 与 cuDNN。装机步骤（换 torch 构建、装 transformers、下权重）已由模块机制兜掉（§4）——本机 2026-09-20 实测 torch 已是 CUDA 版，模块又在选中时后台自动装依赖，故这一步无需手工执行，仅作离线/排障路径（是否需换装 torch 的判据：`ballontrans_pylibs_win\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"`，版本号带 `+cpu` 才需按驱动档位重装 CUDA 版 torch 并连带 torchvision）；权重 15 个文件（仅 safetensors 约 1.9GB 大）落 `data/models/paddleocr_vl_manga/`，目录名与模块常量一致，hf 直连超时、`set HF_ENDPOINT=https://hf-mirror.com` 后 `huggingface_hub.snapshot_download` 可达。应用内验收：选 `paddleocr_vl_manga` + device=cuda 跑一页，与 `paddleocr_v6_onnx` 的原文结果对比，重点看**竖排块、拟声词、密集旁白**；一把跑完的端到端脚本是 `scripts/probes/ocr_vl_manga_accept.py`。

## 6. 风险与待验证项

| # | 项 | 结论 |
| --- | --- | --- |
| 1 | 实际速度/显存 | ✅ **已实测**（§3）：加载 3.8s、峰值 2.51GB、单块中位 192ms、约 6s/页；原文「慢两个数量级」的估算偏悲观 |
| 2 | 竖排识别质量 | 训练数据含竖排（Manga109-s），理论上没问题；2026-09-20 抽验那一页没有竖排块，**这一项仍需人看** |
| 3 | 全半角/部首变体误差 | 官方自述的主要误差类型（§2）。可评估：喂翻译前做 NFKC 归一化（注意会连带全角字母数字转半角），或交给 LLM 翻译器自行消化——**倾向后者，先不动** |
| 4 | 批量左 padding | 与 mrope 的兼容性未验证（§4），**落地时因此把 `batch_size` 默认设为 1**（逐块），要试批量得显式调大并逐块比对结果 |
| 5 | HF 下载可达性 | 直连超时、镜像可用（本机实测）；镜像链路**已内建**（§4） |
| 6 | transformers 版本漂移 | 必须 pin `>=4.57.1,<5`；transformers 5.x 移除 trust_remote_code 相关行为的风险需重审。**另加一条**：`add_prefix_space` 触发的 slow 路径依赖 transformers 内部行为，升级后要重跑 §3 那行 processor 加载 |
| 7 | use_cache 陷阱 | 忘传 True 慢数倍（§2），已写进模块实现（`modules/ocr/ocr_vl_manga.py::PaddleOCRVLManga` 已显式传） |
| 8 | 老 GPU | Pre-Ampere（20 系及更早）不支持 bf16 → 用 float16。2026-09-20：本机 CC 12.0 原生支持 bf16，按 bf16 走 |
| 9 | 与 onnxruntime CUDA 会话并存 | 本路线只有 torch 一个运行时，无 paddle 共存问题；本模块峰值仅 2.5GB，与检测/修复会话叠加在 11.9GB 卡上仍有余量（§3 实测） |
| 10 | 整页模式 | ❌ **实测不可用**（同页 27 块 → 只出 11 行、无坐标）⇒ 检测必须由本项目检测器提供，本模块不能当「二合一」（§1） |
| 11 | tokenizer 在缺 sentencepiece 时直接崩 | ✅ **已定案**：加载时传 `add_prefix_space=None`（§3）。它修的是「跑管线时 OCR 失败」这个已发生过的故障；权重更新后重跑 `scripts/probes/ocr_vl_tokenizer.py` |

## 7. 备选路线与放弃理由

- **paddlepaddle-gpu + paddleocr 3.x 官方管线**：不推荐。Windows 上 paddle GPU 轮子对 CUDA/cuDNN 版本挑剔、装机成本高；与 torch 双运行时并存显存翻倍；官方 VL 管线要拖 PP-DocLayoutV2 版面模型，而漫画场景官方自己都说明阅读顺序不同、不建议配版面模型；且本微调仓库只发布了 transformers 格式 safetensors，喂 paddle 管线还需先转格式。
- **vLLM 等服务化推理框架**：Linux only，桌面应用不适用（WSL2 属过度设计）。
- **ONNX 导出**：0.9B 自回归 VLM + NaViT 动态分辨率 + mrope，导出与数值对齐成本极高，放弃。

## 8. 参考链接

- 权重（HF）：https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga （国内镜像：https://hf-mirror.com/jzhang533/PaddleOCR-VL-For-Manga ）；训练/推理代码（GitHub）：https://github.com/jzhang533/PaddleOCR-VL-For-Manga
- 官方教程（pfcc.blog）：https://pfcc.blog/posts/paddleocr-vl-for-manga ；在线 demo（HF Space）：https://huggingface.co/spaces/jzhang533/paddleocr-vl-for-manga-demo
- 基座模型：https://huggingface.co/PaddlePaddle/PaddleOCR-VL ；同类参照（合成数据生成所用的 manga-ocr）：https://github.com/kha-white/manga-ocr
