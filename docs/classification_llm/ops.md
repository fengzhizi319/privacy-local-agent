# 本地多模态大模型分类分级运维手册

## 1. 模型下载

### 1.1 一键下载脚本

项目内置模型下载脚本，优先通过 ModelScope 下载，失败自动切换 Hugging Face 镜像站。

```bash
cd /home/charles/code/sfwork/privacy-local-agent
source .venv/bin/activate

# 安装可选下载工具（二选一即可）
pip install modelscope huggingface_hub

# 执行下载
python -m privacy_local_agent.privacy.download_model
```

默认保存路径：

```text
/home/charles/code/sfwork/privacy-local-agent/.models/Qwen2-VL-2B-Instruct
```

下载完成后，目录中应包含 `config.json`、`tokenizer.json`、`model.safetensors` 等文件。

### 1.2 手动下载

也可通过 `snapshot_download` 在 Python 中手动下载：

```python
from modelscope import snapshot_download

local_dir = "/home/charles/code/sfwork/privacy-local-agent/.models/Qwen2-VL-2B-Instruct"
snapshot_download("Qwen/Qwen2-VL-2B-Instruct", local_dir=local_dir)
```

## 2. 环境准备

### 2.1 安装 ML 依赖

```bash
pip install -r requirements-ml.txt
```

核心依赖包括：

| 包 | 说明 |
|---|---|
| `torch>=2.0.0` | 推理后端 |
| `transformers>=4.45.0` | Qwen2-VL 模型与处理器 |
| `accelerate` | 设备映射与显存优化 |
| `pillow` | 图片读取与解码 |
| `qwen-vl-utils` | 视觉信息预处理（可选） |

### 2.2 硬件平台说明

| 平台 | 推理模式 | 说明 |
|---|---|---|
| CUDA | FP16 | 优先启用，显存占用 ≤ 5.5GB |
| MPS (Apple Silicon) | FP32 | 自动检测启用 |
| CPU | FP32 | 兜底模式，速度较慢 |

## 3. 运行配置

### 3.1 启动服务

大模型分类能力内置于分类服务，启动方式与其他原语一致。

```bash
# REST 服务
python -m privacy_local_agent.main

# gRPC 服务
python -m privacy_local_agent.grpc_server

# REST + gRPC
python -m privacy_local_agent.server
```

### 3.2 通过 Profile 启用 LLM 层

默认情况下 `enable_llm=false`，大模型仅在低置信度时作为兜底触发。若希望优先启用大模型定级，可在 YAML profile 中设置：

```yaml
primitives:
  classification:
    enable_llm: true
```

加载方式：

```bash
export PRIVACY_PROFILE=/path/to/privacy-profile.yaml
python -m privacy_local_agent.main
```

### 3.3 REST 调用示例

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/classify/field \
  -H "Content-Type: application/json" \
  -d '{
    "field_name": "medical_image",
    "value": "/path/to/medical_report.png",
    "params": {"enable_llm": true}
  }'
```

## 4. 参数建议

| 参数 | 建议 |
|---|---|
| `enable_llm` | 生产环境按需开启；无 GPU 时建议关闭，仅在低置信度触发兜底。 |
| `default_level` | 无规则命中时的默认等级，建议保持 `L3`。 |
| `enable_small_ner` | 已下载 NER 模型时开启，可提升 Layer-2 召回。 |

## 5. 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `本地模型未找到，请先运行下载脚本` | `.models/Qwen2-VL-2B-Instruct` 目录不存在或为空 | 执行 `python -m privacy_local_agent.privacy.download_model` |
| `No module named 'torch'` | 未安装 ML 依赖 | 安装 `requirements-ml.txt` |
| `No module named 'transformers'` | 未安装 transformers | `pip install transformers>=4.45.0` |
| 图片输入被识别为纯文本 | 图片路径不存在或后缀不被识别 | 确认路径存在，后缀为 `.jpg/.jpeg/.png/.bmp/.webp`；Base64 图片确保长度 > 100 字符 |
| LLM 输出 JSON 解析失败 | 模型输出包含额外说明文字 | 系统自动回退保守定级，可检查日志查看原始输出 |
| CUDA OOM | 显存不足 | 关闭其他进程，或使用 CPU 推理；长图可先裁剪 |
| 推理速度慢 | 使用 CPU 或未开启硬件加速 | 检查 `torch.cuda.is_available()` 或 `torch.backends.mps.is_available()` |
