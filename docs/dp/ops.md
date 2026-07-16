# 差分隐私运维手册

## 1. 调用示例

### Laplace count

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/count \
  -H "Content-Type: application/json" \
  -d '{"values":[1,0,1,1,0],"params":{"epsilon":1.0}}'
```

### Gaussian sum with clipping

```bash
curl -X POST http://127.0.0.1:8079/v1/privacy/dp/sum \
  -H "Content-Type: application/json" \
  -d '{
    "values":[1,2,3,100],
    "params":{
      "epsilon":1.0,
      "delta":1e-6,
      "mechanism":"gaussian",
      "clip_lower":0.0,
      "clip_upper":10.0
    }
  }'
```

## 2. 参数建议

| 参数 | 建议 |
|---|---|
| `epsilon` | 1.0 为常用默认值；敏感数据建议 0.1~0.5。 |
| `delta` | 必须 `< 1/n^2`；典型值 `1e-6`。 |
| `clip_lower/upper` | 根据业务先验设置；可通过离线分位数估计。 |
| `mechanism` | 小敏感度用 Laplace；需要更小噪声且可接受 delta 时用 Gaussian。 |

## 3. 故障排查

| 现象 | 原因 |
|---|---|
| `400 clip bounds required` | Gaussian sum/mean 未提供 clip。 |
| `Privacy budget exhausted` | 累计 epsilon 或 delta 超过命名空间上限。 |
| `delta must be positive for gaussian` | Gaussian 请求 delta=0。 |
