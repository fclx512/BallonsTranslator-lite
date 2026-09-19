# paddle-ocr-for-manga 接入调研

> 调研日期 2026-09-19。应用户「添加 paddle-ocr-for-manga 模型支持」需求做的预研。**本机无 CUDA 环境，未做任何实测**，所有性能数字均为估算或官方数据；部署与验证在配置齐全的主力机上进行。

## 0. 结论先行

- 经检索认定，该需求指 **jzhang533 的 PaddleOCR-VL-For-Manga**（GitHub 训练仓库 + HuggingFace 权重，见 §7 链接）。若用户实际指的是另一个同名项目，本文需按其仓库重写。
- 最关键的事实：官方训练仓库的推理示例与 HF demo 都直接用 **HuggingFace transformers（PyTorch）加载**，权重仓库对 transformers 自包含——**整条路线不需要安装 paddlepaddle，也不需要安装系统级 CUDA Toolkit / cuDNN**，只需要 CUDA 版 torch 轮子（轮子自带 CUDA 运行时，仅要求 NVIDIA 驱动）。名字里的 "Paddle" 只是血统。
- 对接方式：仿现有识别-only 模块新增一个 OCR 模块，继续用本项目自己的检测器（官方明确说漫画场景不要配其版面模型），只换识别器。工作量可控。
- 定位：日文漫画专精的「质量优先」OCR，逐块自回归解码，速度比 onnx 系慢约两个数量级，不建议作默认识别器。

## 1. 模型基本情况

| 项 | 内容 |
| --- | --- |
| 作者 | jzhang533（PaddlePaddle 联合创始人） |
| 基座 | PaddlePaddle/PaddleOCR-VL（0.9B 视觉语言 OCR 模型，官方宣称支持 109 语种） |
| 微调数据 | Manga109-s 文本区域 crop 约 10 万（90% split 训练 / 10% 测试）+ 150 万合成样本 |
| 精度 | Manga109-s 测试集整句准确率 **70%**（基座同测仅 27%） |
| 任务形态 | 输入单个文本区域 crop（非整页）+ prompt `OCR:` → 输出多行日文文本（换行分行） |
| 许可 | Apache 2.0（权重仓库声明） |
| 语言范围 | 日文漫画专精；其他语种/通用文档不适用 |

架构（读自 HF config.json）：SigLIP NaViT 视觉编码器（27 层、hidden 1152、patch 14、动态分辨率，min/max_pixels = 147384 / 2822400）+ 18 层自回归解码器（hidden 1024、GQA 16 注意力头 / 2 KV 头、词表 103424），合计约 0.9B 参数。权重为**单文件** safetensors，约 1.92GB（bf16）。

HF 权重仓库是自包含的：config + auto_map 自定义代码（modeling / processing / image_processing / configuration 四个 py 文件）+ tokenizer + chat template + processor 配置，共 15 个文件，加载时只需 `trust_remote_code=True` 指向本地目录，不需要联网、不需要基座仓库。

## 2. 推理契约与关键参数

调用形态（官方示例与 demo 一致）：把 crop 转 RGB 后与 prompt `OCR:` 组成 chat message，`processor.apply_chat_template` + `processor(...)` 出张量，`model.generate` 贪心解码，`batch_decode` 取新增 token 即识别文本。

| 参数 | 取值 | 说明 |
| --- | --- | --- |
| dtype | bfloat16（Ampere 及以后）；老卡改 float16 | 权重即 bf16 |
| attn_implementation | sdpa（默认即可，显式传更稳） | 远端代码声明支持 sdpa；flash-attn 2 为可选加速，代码里 `is_flash_attn_2_available()` 不满足时自动跳过——**Windows 不装 flash-attn** |
| use_cache | **必须显式传 True** | config 与 generation_config 都写死 `use_cache: false`（训练侧习惯），不覆盖则自回归解码不走 KV cache，慢数倍。官方两份推理代码均显式传了 True |
| max_new_tokens | 256 | 气泡 crop 足够；密集旁白可调大（demo 用 2048）。建议做成模块参数 |
| do_sample | False | 贪心 |
| min/max_pixels | 147384 / 2822400 | 每 28×28px 合并为 1 个视觉 token（patch 14 × spatial merge 2），即单 crop 约 188～3600 视觉 token；可下调 max_pixels 换速度 |

识别结果无置信度分数（生成式模型只有 token 概率）；输出保留模型原样，包括官方自述的典型误差类型：全角/半角混排（`！？` vs `!?`、`ＯＫ` vs `ok`）与 CJK 部首变体字（`⼈` U+2F08 vs `人` U+4EBA）。

## 3. 依赖与资源开销

对照本机自带环境实测（ballontrans_pylibs_win，python 3.12.4）：torch 2.12.1+**cpu**、transformers **未安装**、einops 0.8.2 已有、Pillow 10.4 已有、numpy 2.5.0 已有。

| 依赖 | 要求 | 说明 |
| --- | --- | --- |
| torch | **CUDA 12.x 构建** | 轮子自带 CUDA 运行时 DLL，只要求 NVIDIA 驱动；本项目 onnxruntime-gpu 路径本就借 torch lib 目录的 CUDA 12 DLL（见 modules/ocr/ocr_onnx.py::PaddleOCRv6ONNX 的 `_ensure_cuda_dll_path`），主力机若已在用 GPU 检测/修复，说明 torch 已是 CUDA 版，**无需再动** |
| torchvision | 与 torch 同源同版 | 仅当主力机在用 ysgyolo/ultralytics 检测器时需要，重装 torch 时必须连带 |
| transformers | >=4.57.1, <5 | config 声明 transformers_version 4.57.1；远端代码用到 GradientCheckpointingLayer、check_model_inputs 等 4.56+ API。安装连带 tokenizers / safetensors / huggingface-hub |
| accelerate | 仅 `device_map="auto"` 需要 | 用 `.to(device)` 加载则不需要 |
| flash-attn | 不装 | 见 §2 |

显存估算：权重 1.92GB + CUDA 上下文约 0.3～0.5GB + 激活与 KV cache（KV 极小：18 层 × 2 KV 头 × 128 维）≈ **峰值 3GB 上下**。显存 ≥6GB 可跑；与检测/修复模型共存建议 ≥8GB，或配合设置页「释放内存」分阶段用。

速度估算（**全部待主力机实测**）：每块自回归解码约 20～60 token，30 系卡估 0.3～1.5s/块；一页 10～40 块即 **10～60s/页**。CPU 推理不实用（估 10s+/块），本模块应按 GPU-only 对待，device 缺省即 cuda。

## 4. 与本项目模块系统的对接方案（供实现阶段参考）

新增识别-only OCR 模块（建议注册名 `paddleocr_vl_manga`，文件放 modules/ocr/ 下按 ocr_ 前缀命名规范），继承 modules/ocr/base.py::OCRBase，形态完全参照 modules/ocr/ocr_onnx.py::PaddleOCRv6ONNX：

- **整块裁剪，不逐行**：VL 模型在 Manga109-s 的整块文本区域 crop 上训练，块内多行/多列一起喂才能借到模型自己的阅读顺序。取块内所有 lines 点集的 union 外接四边形，用 onnxocr.utils.get_rotate_crop_image 做透视校正。**不要**用 utils/textblock.py::TextBlock.get_transformed_region——那是渲染用的，会把竖排旋转成横排；竖排 crop 必须保持原始朝向。
- 批量：整页所有块拼一个 batch 一次 generate。需把 processor.tokenizer 的 padding_side 设为 "left"（生成式批量推理的标配）；**批量左 padding 与该模型 mrope 位置编码的兼容性待实测**，不行就退化为逐块推理（质量不变，只慢些）。
- 输出：按换行 split 后写入 blk.text（List[str]），与现有 OCR 契约一致；`ocr_img` 整图模式照 ocr_onnx 先例拒绝实现。
- 置信度标签：生成式无分数，utils/block_tags.py::apply_ocr_confidence_tag 无从挂起——「OCR 置信度低」自动标签在本模块下不生效。后续可用输出 token 平均 logprob 造伪分数，另行评估。
- 模块机制全部现成，无需新造：
  - `requires_packages = ["transformers>=4.57.1,<5"]` → modules/base.py::BaseModule.ensure_dependencies 在首次 load_model 时自动安装（torch CUDA 版换装无法自动化，见 §5 第 1 步）。
  - `download_file_list` + modules/base.py::BaseModule._ensure_model_files 支持首次加载自动下载 15 个文件到 data/models/paddleocr_vl_manga/；注意 utils/download_util.py::download_and_check_files 默认走 torch_hub 下载，HF 直连在国内网络经常超时（本机实测直连超时、hf-mirror.com 可达），实现时要验证/给镜像方案，也允许用户手动放置。
  - device 参数用 modules/base.py::DEVICE_SELECTOR；`_load_model_keys = {"model", "processor"}`；unload_model 置 None 即可。
  - params 的 description 自动进 i18n 提取链（scripts/i18n_common.py::extract_param_descriptions）。
- UI 定位：与 paddleocr_v6_onnx（通用、快）、mit 系并列的日文漫画质量优先选项；默认检测器、默认 OCR 均不变。

## 5. 主力机部署清单（Windows）

前置：NVIDIA 驱动足够新（CUDA 12.x 轮子一般要求 ≥ 550 一档）；**不需要**安装 CUDA Toolkit 与 cuDNN。

1. **核对 torch 构建**：`ballontrans_pylibs_win\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available())"`。版本号带 `+cpu` 才需要换装：先看 site-packages 里是否已装 torchvision（ultralytics 检测器依赖它），随后 `python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126` 一次性覆盖安装（cu126/cu128 按驱动选，版本与原 torch 大版本保持同档）。已是 CUDA 版则跳过。
2. **装 transformers**：`python.exe -m pip install "transformers>=4.57.1,<5"`。
3. **下载权重**到项目 data/models/paddleocr_vl_manga/（目录名与将来模块实现保持一致）：
   - 命令行（推荐）：`set HF_ENDPOINT=https://hf-mirror.com` 后执行 `ballontrans_pylibs_win\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('jzhang533/PaddleOCR-VL-For-Manga', local_dir='data/models/paddleocr_vl_manga')"`（transformers 装好后 huggingface_hub 即可用）。
   - 或浏览器从镜像页逐文件下载（15 个文件，仅 safetensors 约 1.9GB 大）。
   - 实测（2026-09-19）：huggingface.co 直连超时，hf-mirror.com 可达；主力机若同网络环境务必带 HF_ENDPOINT。
4. **最小自测脚本**（模型与 processor 均从本地目录加载，torch.cuda.is_available() 应为 True）：

   ```python
   import torch
   from PIL import Image
   from transformers import AutoModelForCausalLM, AutoProcessor

   model = AutoModelForCausalLM.from_pretrained(
       "data/models/paddleocr_vl_manga",
       trust_remote_code=True, dtype=torch.bfloat16,
       attn_implementation="sdpa",
   ).to("cuda").eval()
   processor = AutoProcessor.from_pretrained(
       "data/models/paddleocr_vl_manga", trust_remote_code=True, use_fast=True
   )

   img = Image.open("examples/02.png").convert("RGB")
   messages = [{"role": "user", "content": [
       {"type": "image", "image": img}, {"type": "text", "text": "OCR:"}]}]
   text = processor.apply_chat_template(messages, tokenize=False,
                                        add_generation_prompt=True)
   inputs = processor(text=[text], images=[img], return_tensors="pt")
   inputs = {k: (v.to("cuda") if isinstance(v, torch.Tensor) else v)
             for k, v in inputs.items()}
   with torch.inference_mode():
       out = model.generate(**inputs, max_new_tokens=256,
                            do_sample=False, use_cache=True)
   ans = processor.batch_decode(out[:, inputs["input_ids"].shape[1]:],
                                skip_special_tokens=True)[0]
   print(ans)
   ```

   （示例图可直接用训练仓库 data/images 下的样例；老卡把 bfloat16 换 float16。）
5. **应用内验收**：实现模块后选 `paddleocr_vl_manga` + device=cuda 跑一页，与 paddleocr_v6_onnx 的原文结果对比；重点看竖排块、拟声词、密集旁白。

## 6. 风险与待验证项（本机无法验证，全部待主力机）

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 实际速度/显存 | §3 全为估算，先跑 §5 自测脚本量 |
| 2 | 竖排识别质量 | 训练数据含竖排（Manga109-s），理论上没问题，需真机确认 |
| 3 | 全半角/部首变体误差 | 官方自述的主要误差类型（§2）。可评估：喂翻译前做 NFKC 归一化（注意会连带全角字母数字转半角），或交给 LLM 翻译器自行消化——倾向后者，先不动 |
| 4 | 批量左 padding | 与 mrope 的兼容性待实测（§4） |
| 5 | HF 下载可达性 | 直连超时、镜像可用（本机实测）；自动下载链路同样要考虑镜像 |
| 6 | transformers 版本漂移 | 必须 pin `>=4.57.1,<5`；transformers 5.x 移除 trust_remote_code 相关行为的风险需重审 |
| 7 | use_cache 陷阱 | 忘传 True 慢数倍（§2），写进模块实现 |
| 8 | 老 GPU | Pre-Ampere（20 系及更早）不支持 bf16 → 用 float16 |
| 9 | 同进程双框架？ | 本路线只有 torch 一个运行时，无 paddle 共存问题；但注意与 onnxruntime CUDA 会话并存时的显存峰值 |

## 7. 备选路线与放弃理由

- **paddlepaddle-gpu + paddleocr 3.x 官方管线**：不推荐。Windows 上 paddle GPU 轮子对 CUDA/cuDNN 版本挑剔，装机成本高；与 torch 双运行时并存显存翻倍；官方 VL 管线要拖 PP-DocLayoutV2 版面模型，而漫画场景官方自己都说明阅读顺序不同、不建议配版面模型；且本微调仓库只发布了 transformers 格式 safetensors，喂 paddle 管线还需先转格式。
- **vLLM 等服务化推理框架**：Linux only，桌面应用不适用（WSL2 属过度设计）。
- **ONNX 导出**：0.9B 自回归 VLM + NaViT 动态分辨率 + mrope，导出与数值对齐成本极高，放弃。

## 8. 参考链接

- 权重（HF）：https://huggingface.co/jzhang533/PaddleOCR-VL-For-Manga （国内镜像：https://hf-mirror.com/jzhang533/PaddleOCR-VL-For-Manga ）
- 训练/推理代码（GitHub）：https://github.com/jzhang533/PaddleOCR-VL-For-Manga
- 官方教程（pfcc.blog）：https://pfcc.blog/posts/paddleocr-vl-for-manga
- 在线 demo（HF Space）：https://huggingface.co/spaces/jzhang533/paddleocr-vl-for-manga-demo
- 基座模型：https://huggingface.co/PaddlePaddle/PaddleOCR-VL
- 同类参照（合成数据生成所用的 manga-ocr）：https://github.com/kha-white/manga-ocr
