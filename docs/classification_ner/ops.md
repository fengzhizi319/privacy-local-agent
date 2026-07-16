# 本地轻量级 Small-NER 运维与部署手册 (Operations Guide)

## 1. 系统要求与环境准备 (Prerequisites)

### 1.1 依赖库安装
为了启动本地命名实体识别引擎，您需要激活虚拟环境并安装核心科学计算与推理运行时库：
```bash
# 激活虚拟环境
source .venv/bin/activate

# 安装 ONNX 推理运行时与数据计算包
pip install onnxruntime numpy

# （可选）若希望直接使用 ModelScope 官方 Transformers 管道模式，执行：
pip install modelscope torch torchvision transformers addict yapf six
```

> [!NOTE]
> **macOS ARM (Apple Silicon) 平台兼容说明**：
> - **ONNX 极速模式**：官方 `onnxruntime` 已经发布了 native arm64 wheels。在 Apple Silicon 下，使用 `pip install onnxruntime` 即可调用 ARM64 NEON 指令集进行超高速 CPU 推理。
> - **ModelScope 官方管道模式**：macOS 平台只需通过 `pip install torch` 引入标准包即可。系统启动时会通过 `torch.backends.mps.is_available()` 自动检测并启用苹果专用的 **MPS (Metal Performance Shaders) 显卡硬件加速**，内存占用极低。

### 1.2 本地模型目录结构 (Directory Layout)
所有的本地大模型与 NER 文件均存放在项目根目录下的 `.models/` 目录中：
```text
privacy-local-agent/
  ├── .models/
  │    ├── vocab.txt                   # BERT 通用词表文件
  │    ├── raner_cmeee.onnx            # CMeEE NER 预导出的 ONNX 模型
  │    └── raner_cmeee/                # （可选）ModelScope 官方 PyTorch 模型全量缓存目录
  │         ├── pytorch_model.bin
  │         ├── config.json
  │         └── ...
```

---

## 2. 模型下载操作 (Model Download Steps)

### 2.1 首选方式：ModelScope 社区一键下载
我们提供了一键式高速本地下载脚本，这将在国内直接拉取 ModelScope 的权重并缓存：
```bash
PYTHONPATH=. .venv/bin/python privacy_local_agent/privacy/download_ner_model.py
```
*提示：如果已安装 `modelscope` 库，该脚本会自动拉取全量文件并将 `vocab.txt` 拷贝至 `.models/vocab.txt`，非常方便。*

### 2.2 备选方式：Hugging Face 授权下载
如果您没有安装 `modelscope` 库，脚本会通过 urllib 自动回退至 Hugging Face 镜像站下载。由于存放的仓库处于受保护状态，您需要在运行命令时传入您在 Hugging Face 拥有的 Token（`HF_TOKEN`）：
```bash
HF_TOKEN=您的_HuggingFace_Access_Token PYTHONPATH=. .venv/bin/python privacy_local_agent/privacy/download_ner_model.py
```

---

## 3. 分类分级服务配置 (Configuration)

### 3.1 YAML 配置文件启用
您可以在您的数据分级 Profile YAML（例如 `config.yaml`）中，加入或修改以下参数，以全局启用第二层 NER 拦截：
```yaml
classification:
  enableSmallNer: true             # 启用 Small-NER 第二层引擎
  defaultLevel: L3                 # 未匹配到规则/实体时的默认兜底敏感级
```

### 3.2 动态 API 请求级启用
在向网关发送分类请求时，您可以通过 `params` 对象针对单次查询动态启用或关闭 NER 检测：
- **REST API** (`POST /v1/privacy/mask` 或分类端点)：
  ```json
  {
    "field_name": "clinical_note",
    "value": "患者张三，诊断为HIV阳性",
    "params": {
      "enable_small_ner": true
    }
  }
  ```

---

## 4. 故障排除与常见错误 (Troubleshooting)

### 4.1 错误日志：`未找到本地 ONNX 模型文件`
- **现象**：后台打印 WARNING `ONNX Small-NER 初始化失败`，但分类请求正常通过，只是未触发 NER 实体打标。
- **排查**：检查 `.models/raner_cmeee.onnx` 文件是否存在。
- **解决**：运行 `download_ner_model.py` 重新同步模型文件。

### 4.2 错误日志：`HTTP Error 401: Unauthorized`
- **现象**：运行下载器脚本时失败退出，报 401。
- **原因**：下载受保护的 Hugging Face 模型时没有传入 Token 或 Token 无效。
- **解决**：优先安装 `pip install modelscope` 使用免 Token 的魔搭社区源，或者在终端运行命令时正确配置 `HF_TOKEN` 环境变量。

### ### 4.3 报错：`ModuleNotFoundError: No module named 'six'` 或 `'addict'`
- **现象**：使用 ModelScope 模式进行推理时，Lazy loading 警告模块缺失并自动回退。
- **原因**：Python 3.13 缺少一些 ModelScope / Torchvision 的可选下级依赖。
- **解决**：执行 `.venv/bin/pip install six addict yapf` 补充缺失的兼容库。
