# CUDA 环境与索引选择说明

本文记录维护者准备 Windows CUDA 完整环境时的选择和验证边界。普通用户优先下载与显卡
档位匹配的完整环境包；`install_cuda.bat` 是维护工具，不是必需的安装向导。

## 维护工具的安全边界

`install_cuda.bat` 默认运行 `scripts/install_cuda_bundle.py` 的只读检查，显示 GPU 档位、
当前 Torch/TorchVision/ONNX Runtime 状态和待选方案。传入 `--json` 时只输出机器可读检查；
只有显式传 `--install` 才联网更换包内组件。工具只使用项目旁
`ballontrans_pylibs_win/python.exe`，不查找或修改系统 Python、PATH 解释器或其他 venv。

检查会验证实际 import、CUDA 分配及 ONNX Runtime CUDA session。仅列出
`CUDAExecutionProvider` 不代表 CUDA DLL 可正常加载。安装会拒绝 Torch/TorchVision 元数据
不一致或环境混装；完成变更后须重新检查，并对候选完整包进行应用和模型验收。

## 档位映射

档位按 GPU 计算能力（compute capability, CC）判断，不按商品名作为主要依据。实现以
`utils/env_diagnostic.py::pick_cuda_index` 与 `scripts/install_cuda_bundle.py` 为准，
`tests/test_cuda_install_env.py` 锁定脚本与 Python 侧映射的一致性。

| CC 范围 | PyTorch 索引 | CUDA 档 | 当前安装工具的 ONNX Runtime 目标 |
| --- | --- | --- | --- |
| ≥ 10（Blackwell 及更新） | `cu132` | 13.2 | `onnxruntime-gpu==1.27.0` |
| 9.x（Hopper） | `cu130` | 13.0 | `onnxruntime-gpu==1.27.0` |
| 6.x–8.9（含 Pascal、Turing、Ampere、Ada） | `cu126` | 12.6 | `onnxruntime-gpu==1.20.1` |
| < 6 | 不支持 | — | 使用 CPU 档 |

该表反映当前源码工具的选择，不保证新驱动、显卡或包组合已验收。`nvidia-smi
--query-gpu=compute_cap --format=csv,noheader` 可查询 CC。诊断界面可按型号回退展示；安装工具
严格要求查询成功，旧驱动不支持查询时拒绝改动，不猜测档位。

## Torch 与 ONNX Runtime 不能只按大版本混配

PyTorch 官方提供不同 CUDA wheel 索引；安装命令必须匹配当前 Torch/TorchVision 配对和
完整环境档位。请在升级前核对 [PyTorch Previous Versions](https://pytorch.org/get-started/previous-versions/)
及对应版本的安装说明。

ONNX Runtime GPU 包也有 CUDA/cuDNN 运行时要求。官方文档说明：较新版本默认以 CUDA 13
构建；若采用 1.27+ 的 CUDA 12 构建，要求 CUDA 12.8。当前工具把 CUDA 12.6 档固定到
`1.20.1`，把 CUDA 13 档固定到 `1.27.0`。未来升级映射时需核对
[ONNX Runtime CUDA Execution Provider 官方说明](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)，
并在目标完整环境中验证，而不能将一个宽泛的 `>=` 范围应用于所有 profile。

`onnxruntime`（CPU）和 `onnxruntime-gpu` 是两个发行名，却提供同一 Python import 与重叠
原生文件。环境可能残留双份 dist-info。因此替换时必须审计并成对处理两个发行名；
`scripts/install_cuda_bundle.py` 负责清理和安装流程，不能手工覆盖后仅凭 pip 显示的版本
判定成功。

工具只转换完整的 `torch==2.13.0+cpu` / `torchvision==0.28.0+cpu` 基线，或保留已经通过
目标 CUDA 检查的组合。缺包、未知组合和元数据/实际文件不一致均应重新取得完整包。
改动前先备齐目标与原版本恢复 wheel；准备失败不改环境，安装或复验失败时尝试从本地
wheel 恢复原版本。恢复失败会明确报错并保留 wheel 目录，不能把这种状态作为发行环境。

## 离线发行环境清单

源码 `runtime_compatibility.json` 声明支持的环境 family、Python 系列和 profile；它表达
兼容范围，不是当前开发环境或发行包通过测试的证明。具体完整包中的
`runtime-manifest.json` 记录 bundle ID、profile、Python 精确版本、实际 distributions 和
随包模型文件哈希。维护者用 `scripts/check_bundle.py --profile cpu|cu126|cu130|cu132`
检查准备好的发行目录；经确认后才可附加 `--write-manifest --bundle-id ID` 生成该包快照。
不要从共享开发环境生成发行快照，也不要把静态检查当作真实推理验收。

## 定位顺序

1. 先运行 `install_cuda.bat` 做只读检查，保存 JSON 或终端结果；不确认方案时不要加
   `--install`。
2. 核对 Torch 与 TorchVision 是否成对、CPU/GPU ORT 是否混装、选中的 CUDA 档是否与 CC
   一致。
3. 若有意安装，显式运行 `install_cuda.bat --install`，随后再次运行只读检查。
4. 对完整包运行 `scripts/check_bundle.py`，再做应用启动、目标模型加载和实际推理验收。

`scripts/check_cuda_env.py` 可用于维护者查询索引与检查任意指定解释器；它是只读诊断，
不替代完整发行包检查。索引内容和官方运行时要求会变动，发版前应直接复核上面的官方文档。
