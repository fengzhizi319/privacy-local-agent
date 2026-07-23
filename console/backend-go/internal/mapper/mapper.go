// Package mapper 是 REST → gRPC 路由映射的核心模块。
//
// 职责：
//   - 维护一张 "REST 路径 → gRPC handler" 的分发表（dispatch table）
//   - 接收前端发来的 JSON 请求体，解析为对应的 protobuf 消息
//   - 调用上游 privacy-local-agent 的 gRPC 方法
//   - 将 protobuf 响应转换为前端可消费的 JSON 数据结构
//
// 设计原则：
//   1. 前端使用统一的 JSON 契约（POST /api/proxy）发送请求
//   2. 本包根据 path 字段识别应调用的 gRPC RPC
//   3. 将 JSON body 转换为对应的 protobuf 请求消息
//   4. 调用 gRPC 客户端获取响应
//   5. 将 protobuf 响应转换为前端可展示的 JSON 数据
//
// 路径分类：
//   - /v1/privacy/health          → 健康检查
//   - /v1/privacy/mask*           → 数据脱敏（单字段/整条记录/批量/DataFrame/哈希）
//   - /v1/privacy/dp/*            → 差分隐私（count/sum/mean/histogram 及变体）
//   - /v1/privacy/ldp/*           → 本地差分隐私（扰动 + 频率估计）
//   - /v1/privacy/k_anonymize/*   → K-匿名（记录级/表级/DataFrame 级）
//   - /v1/privacy/qol/*           → 查询混淆（单条/批量）
//   - /v1/privacy/classify/*      → 数据分类（字段/记录/表/异步/SecretFlow/复核）
//   - /v1/privacy/profile/*       → 个性化配置推荐
package mapper

import (
	// context：用于传递 gRPC 调用的上下文（超时、取消、元数据等）
	"context"
	// encoding/json：用于解析前端发来的 JSON 请求体、序列化响应数据
	"encoding/json"
	// fmt：用于格式化错误信息（如 "unsupported gRPC path"）
	"fmt"
	// regexp：用于匹配动态路径（如 /v1/privacy/classify/jobs/{job_id}）
	"regexp"
	// strings：用于字符串处理（ToLower/TrimSpace 等大小写/空白规范化）
	"strings"

	// protojson：Google 官方的 protobuf ↔ JSON 转换器，
	// 使用 UseProtoNames 选项保持字段名为 protobuf 原始名称（而非 camelCase）
	"google.golang.org/protobuf/encoding/protojson"
	// proto：protobuf 消息的顶层接口，marshalProto 函数参数类型
	"google.golang.org/protobuf/proto"

	// pb：由 proto/privacy.proto 生成的 gRPC 代码，
	// 包含所有 RPC 方法定义（PrivacyServiceClient）和消息类型（各种 Request/Response）
	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

// Handler 定义了单个 REST 路径到 gRPC 调用的映射函数签名。
//
// 每个 path 对应一个 Handler，负责：
//  1. 解析 JSON body 为 Go 变量（通过 decode + getXxx 辅助函数）
//  2. 构造对应的 protobuf 请求消息
//  3. 通过 client 调用上游 agent 的 gRPC 方法
//  4. 将 protobuf 响应转换为前端可消费的 JSON 数据结构（any 类型）
//
// 参数说明：
//   - ctx：请求上下文，携带超时/取消/认证元数据，直接传递给 gRPC 调用
//   - client：上游 agent 的 gRPC 客户端，提供所有 RPC 方法
//   - body：前端发来的原始 JSON 请求体，由各 handler 自行解析
type Handler func(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error)

// Mapper 持有 REST 路径到 gRPC handler 的分发表。
//
// 分发表是一个 map[string]Handler，key 为 REST 路径（如 "/v1/privacy/mask"），
// value 为对应的处理函数。Dispatch 方法根据请求 path 查找 handler 并调用。
type Mapper struct {
	// handlers：静态路径分发表，存储所有固定路径到 handler 的映射
	handlers map[string]Handler
	// jobRE：用于匹配动态路径的正则表达式，
	// 匹配形如 /v1/privacy/classify/jobs/{job_id} 的路径，
	// 其中 job_id 为异步分类任务的唯一标识
	jobRE *regexp.Regexp
}

// New 创建并返回一个 Mapper 实例，内置所有支持的 REST → gRPC 路径映射。
//
// 执行逻辑：
//   1. 初始化 jobRE 正则，用于匹配异步分类任务的动态路径
//   2. 构建 handlers 分发表，将每个固定路径绑定到对应的 handler 方法
//   3. 返回就绪的 Mapper 实例
//
// 路径分组（共约 40 个端点）：
//   - Health（1 个）：健康检查
//   - Masking（5 个）：单字段脱敏、整条记录脱敏、批量脱敏、DataFrame 脱敏、哈希
//   - DP（16 个）：count/sum/mean/histogram 及 noisy/chunked/aggregate/vector/adaptive/groupby 变体
//   - LDP（4 个）：二进制扰动、分类扰动、二进制频率估计、分类直方图估计
//   - K-Anonymity（3 个）：记录级、表级、DataFrame 级 K-匿名
//   - Query Obfuscation（2 个）：单条/批量查询混淆
//   - Classification（7 个）：字段/记录/表/异步表/SecretFlow/复核确认/复核导出
//   - Profile（1 个）：个性化配置推荐
func New() *Mapper {
	m := &Mapper{
		// 编译动态路径正则：匹配 /v1/privacy/classify/jobs/{任意非斜杠字符}
		// FindStringSubmatch 返回 [完整匹配, 捕获组(job_id)]
		jobRE: regexp.MustCompile(`^/v1/privacy/classify/jobs/([^/]+)$`),
	}
	// 构建静态路径分发表：每个路径对应一个 handler 方法
	m.handlers = map[string]Handler{
		// ── Health ──────────────────────────────────────────────────
		// 健康检查：调用 agent 的 Health RPC，返回状态与命名空间
		"/v1/privacy/health": m.handleHealth,

		// ── Masking（数据脱敏）──────────────────────────────────────
		// 单字段脱敏：根据字段名自动识别 PII 类型并脱敏
		"/v1/privacy/mask": m.handleMask,
		// 整条记录脱敏：对一条记录的所有字段批量脱敏
		"/v1/privacy/mask_record": m.handleMaskRecord,
		// 批量字段脱敏：对一组同类型字段值批量脱敏
		"/v1/privacy/mask_batch": m.handleMaskBatch,
		// DataFrame 级脱敏：对多行多列的表格数据脱敏
		"/v1/privacy/mask_dataframe": m.handleMaskDataFrame,
		// HMAC 哈希：对值加盐哈希，不可逆脱敏
		"/v1/privacy/hash": m.handleHash,

		// ── DP（差分隐私）──────────────────────────────────────────
		// 基础 DP 统计量（使用 flat DPRequest）：
		"/v1/privacy/dp/count":   m.handleDPCount,   // 差分隐私计数
		"/v1/privacy/dp/sum":     m.handleDPSum,      // 差分隐私求和
		"/v1/privacy/dp/mean":    m.handleDPMean,     // 差分隐私均值
		"/v1/privacy/dp/histogram": m.handleDPHistogram, // 差分隐私直方图
		// Noisy 变体（使用独立的 NoisyXxxRequest，参数更丰富）：
		"/v1/privacy/dp/noisy_count":     m.handleDPNoisyCount,     // 带噪计数
		"/v1/privacy/dp/noisy_sum":       m.handleDPNoisySum,       // 带噪求和
		"/v1/privacy/dp/noisy_mean":      m.handleDPNoisyMean,      // 带噪均值
		"/v1/privacy/dp/noisy_histogram": m.handleDPNoisyHistogram, // 带噪直方图
		// Chunked 变体（分块处理大数据集）：
		"/v1/privacy/dp/chunked_count":     m.handleDPChunkedCount,     // 分块计数
		"/v1/privacy/dp/chunked_sum":       m.handleDPChunkedSum,       // 分块求和
		"/v1/privacy/dp/chunked_mean":      m.handleDPChunkedMean,      // 分块均值
		"/v1/privacy/dp/chunked_histogram": m.handleDPChunkedHistogram, // 分块直方图
		// 高级 DP 功能：
		"/v1/privacy/dp/aggregate":     m.handleDPAggregate,     // 多指标聚合
		"/v1/privacy/dp/vector_sum":    m.handleDPVectorSum,     // 向量求和
		"/v1/privacy/dp/adaptive_clip": m.handleDPAdaptiveClip,  // 自适应裁剪
		"/v1/privacy/dp/groupby":       m.handleDPGroupBy,       // 分组聚合

		// ── LDP（本地差分隐私）────────────────────────────────────
		// 扰动：客户端本地加噪后上报，服务端无法获取原始值
		"/v1/privacy/ldp/perturb/binary":      m.handlePerturbBinary,      // 二进制值扰动（0/1 翻转）
		"/v1/privacy/ldp/perturb/categorical": m.handlePerturbCategorical, // 分类值扰动（随机响应）
		// 估计：服务端根据扰动后的数据估计真实分布
		"/v1/privacy/ldp/estimate/binary":     m.handleEstimateBinary,     // 二进制频率估计
		"/v1/privacy/ldp/estimate/categorical": m.handleEstimateCategorical, // 分类直方图估计

		// ── K-Anonymity（K-匿名）──────────────────────────────────
		// 通过泛化准标识符（QI）使每条记录至少与 K-1 条其他记录不可区分
		"/v1/privacy/k_anonymize/record":    m.handleKAnonymizeRecord,   // 单条记录的 K-匿名检查
		"/v1/privacy/k_anonymize/table":     m.handleKAnonymizeTable,    // 表级 K-匿名（Mondrian 算法）
		"/v1/privacy/k_anonymize/dataframe": m.handleKAnonymizeDataFrame, // DataFrame 级 K-匿名

		// ── Query Obfuscation（查询混淆）──────────────────────────
		// 向真实查询中注入虚假查询（dummy queries），使攻击者无法区分真实意图
		"/v1/privacy/qol/obfuscate":       m.handleObfuscateQuery,       // 单条查询混淆
		"/v1/privacy/qol/obfuscate/batch": m.handleObfuscateQueryBatch,  // 批量查询混淆

		// ── Classification（数据分类）─────────────────────────────
		// 三层分类漏斗：规则引擎 → 小型 NER → 本地 LLM/VLM
		"/v1/privacy/classify/field":          m.handleClassifyField,       // 单字段分类
		"/v1/privacy/classify/record":         m.handleClassifyRecord,      // 整条记录分类
		"/v1/privacy/classify/table":          m.handleClassifyTable,       // 表级分类（同步）
		"/v1/privacy/classify/table/async":    m.handleClassifyTableAsync,  // 表级分类（异步，返回 job_id）
		"/v1/privacy/classify/secretflow":     m.handleClassifySecretFlow,  // SecretFlow 联邦分类
		"/v1/privacy/classify/review/confirm": m.handleConfirmReview,      // 人工复核确认
		"/v1/privacy/classify/review/export":  m.handleExportReviews,      // 复核结果导出

		// ── Profile（个性化配置）────────────────────────────────
		// 根据数据特征推荐最优隐私参数（epsilon、mechanism 等）
		"/v1/privacy/profile/recommend": m.handleRecommendParams,
	}
	return m
}

// Dispatch 根据请求路径查找对应的 handler 并调用，是路由分发的核心入口。
//
// 执行逻辑：
//   1. 先在静态分发表 handlers 中查找精确匹配的 path
//   2. 若未命中，尝试用 jobRE 正则匹配动态路径 /v1/privacy/classify/jobs/{job_id}
//   3. 若仍未命中，返回 "unsupported gRPC path" 错误
//
// 参数说明：
//   - ctx：请求上下文，传递给 handler 和 gRPC 调用
//   - client：上游 agent 的 gRPC 客户端
//   - path：前端请求中的目标路径（如 "/v1/privacy/mask"）
//   - body：前端请求的原始 JSON 请求体
//
// 返回值：
//   - any：handler 返回的 JSON 可序列化数据
//   - error：解析失败或 gRPC 调用失败时的错误
func (m *Mapper) Dispatch(ctx context.Context, client pb.PrivacyServiceClient, path string, body json.RawMessage) (any, error) {
	// 第一步：静态路径精确匹配，O(1) 哈希查找
	if handler, ok := m.handlers[path]; ok {
		return handler(ctx, client, body) // 找到则直接调用
	}
	// 第二步：动态路径正则匹配（异步分类任务查询）
	// FindStringSubmatch 返回 ["/v1/privacy/classify/jobs/abc123", "abc123"]
	if matches := m.jobRE.FindStringSubmatch(path); len(matches) == 2 {
		// matches[1] 为捕获组，即 job_id
		return m.handleGetClassificationJob(ctx, client, matches[1])
	}
	// 第三步：所有匹配均失败，返回错误
	return nil, fmt.Errorf("unsupported gRPC path: %s", path)
}

// ---------------------------------------------------------------------------
// JSON 辅助函数
//
// 以下函数用于从 json.Unmarshal 后的 map[string]any 中安全地提取各类字段值。
// 由于 Go 的 json.Unmarshal 将 JSON number 统一解码为 float64，
// 因此数值类提取函数需要同时处理 float64/int/int64 三种类型。
// 所有函数均为“安全提取”：字段不存在或类型不匹配时返回默认值/nil，不会 panic。
// ---------------------------------------------------------------------------

// decode 将原始 JSON body 解析为通用的 map[string]any。
//
// 执行逻辑：
//   1. body 为空时返回空 map（不报错），允许无参请求
//   2. 调用 json.Unmarshal 解析为 map[string]any
//   3. 解析失败时返回带上下文的错误信息
//
// 为什么用 map[string]any 而非具体结构体：
//   前端发送的 JSON 字段名与 protobuf 字段名一致，但类型可能不完全匹配
//   （如 JSON number → float64），使用通用 map 可灵活处理类型转换
func decode(body json.RawMessage) (map[string]any, error) {
	// body 为空（nil 或 ""）时返回空 map，避免后续 nil 判断
	if len(body) == 0 {
		return map[string]any{}, nil
	}
	var v map[string]any
	// 解析 JSON 到通用 map，json.Number 默认解码为 float64
	if err := json.Unmarshal(body, &v); err != nil {
		return nil, fmt.Errorf("invalid JSON body: %w", err)
	}
	return v, nil
}

// getString 从 map 中安全提取字符串字段，不存在或类型不匹配时返回默认值。
//
// 参数：
//   - m：decode 返回的通用 map
//   - key：要提取的字段名
//   - def：字段不存在或类型不匹配时的默认返回值
func getString(m map[string]any, key, def string) string {
	// 检查 key 是否存在
	if v, ok := m[key]; ok {
		// 类型断言为 string，成功则返回
		if s, ok := v.(string); ok {
			return s
		}
	}
	// 字段不存在或类型不匹配，返回默认值
	return def
}

// getFloat64 从 map 中安全提取数值字段并转为 float64。
//
// 为什么需要处理多种数值类型：
//   json.Unmarshal 默认将 JSON number 解码为 float64，
//   但某些场景下 map 中的值可能是 int 或 int64（如手动构造的 map），
//   因此需要同时处理三种情况以确保健壮性。
func getFloat64(m map[string]any, key string, def float64) float64 {
	if v, ok := m[key]; ok {
		// 使用 type switch 处理不同数值类型
		switch n := v.(type) {
		case float64:
			return n // JSON number 的默认类型
		case int:
			return float64(n) // 手动构造的 int
		case int64:
			return float64(n) // 大整数场景
		}
	}
	return def
}

// getInt32 从 map 中安全提取数值字段并转为 int32。
//
// 用于 protobuf 中 int32 类型的字段（如 k、max_depth、num_dummies 等）。
// 注意：float64 → int32 会截断小数部分。
func getInt32(m map[string]any, key string, def int32) int32 {
	if v, ok := m[key]; ok {
		switch n := v.(type) {
		case float64:
			return int32(n) // JSON number → int32（截断小数）
		case int:
			return int32(n)
		case int64:
			return int32(n)
		}
	}
	return def
}

// getStrings 从 map 中安全提取字符串数组字段。
//
// 典型用途：提取 field_names、values、categories 等字符串列表。
// 执行逻辑：遍历 []any 数组，仅保留可断言为 string 的元素。
func getStrings(m map[string]any, key string) []string {
	if v, ok := m[key]; ok {
		// JSON 数组解码为 []any
		if arr, ok := v.([]any); ok {
			// 预分配容量以避免多次扩容
			out := make([]string, 0, len(arr))
			for _, item := range arr {
				// 仅保留字符串类型的元素，跳过非字符串
				if s, ok := item.(string); ok {
					out = append(out, s)
				}
			}
			return out
		}
	}
	return nil
}

// getFloats 从 map 中安全提取 float64 数组字段。
//
// 典型用途：提取 values（差分隐私的数值列表）等浮点数数组。
// 支持 JSON number（float64）和 int/int64 混合数组。
func getFloats(m map[string]any, key string) []float64 {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]float64, 0, len(arr))
			for _, item := range arr {
				// 逐个元素按类型转换为 float64
				switch n := item.(type) {
				case float64:
					out = append(out, n)
				case int:
					out = append(out, float64(n))
				case int64:
					out = append(out, float64(n))
				}
			}
			return out
		}
	}
	return nil
}

// getIntSlice 从 map 中安全提取 int32 数组字段。
//
// 典型用途：提取 LDP 二进制扰动中的 values（0/1 整数数组）。
func getIntSlice(m map[string]any, key string) []int32 {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]int32, 0, len(arr))
			for _, item := range arr {
				// 逐个元素按类型转换为 int32
				switch n := item.(type) {
				case float64:
					out = append(out, int32(n))
				case int:
					out = append(out, int32(n))
				case int64:
					out = append(out, int32(n))
				}
			}
			return out
		}
	}
	return nil
}

// getStringMap 从 map 中安全提取 map[string]string 字段。
//
// 典型用途：提取 record（脱敏用的字段名→值映射）。
// JSON object 解码为 map[string]any，需逐个转换值为 string。
func getStringMap(m map[string]any, key string) map[string]string {
	if v, ok := m[key]; ok {
		// JSON object 解码为 map[string]any
		if mm, ok := v.(map[string]any); ok {
			// 预分配容量
			out := make(map[string]string, len(mm))
			for k, val := range mm {
				// 仅保留值为字符串的键值对
				if s, ok := val.(string); ok {
					out[k] = s
				}
			}
			return out
		}
	}
	return nil
}

// getRecordEntries 从 map 中提取 RecordEntry 列表（protobuf 消息数组）。
//
// 前端发送的 JSON 格式：
//   { "data": [{"fields": {"name": "Alice", "email": "a@b.com"}}, ...] }
//
// 执行逻辑：
//   1. 提取 key 对应的 []any 数组
//   2. 遍历每个元素，找到 "fields" 子对象
//   3. 将 "fields" 内的 string 值提取为 map[string]string
//   4. 构造 pb.RecordEntry{Fields: map[string]string}
//
// 典型用途：mask_dataframe、k_anonymize/table、classify/table 等表格类接口
func getRecordEntries(m map[string]any, key string) []*pb.RecordEntry {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.RecordEntry, 0, len(arr))
			for _, item := range arr {
				// 每个数组元素应为一个 map（对应一条记录）
				if mm, ok := item.(map[string]any); ok {
					fields := make(map[string]string)
					for k, val := range mm {
						// 只处理 "fields" 键，其值为字段名→字段值的映射
						if k == "fields" {
							if fmap, ok := val.(map[string]any); ok {
								for fk, fv := range fmap {
									if fs, ok := fv.(string); ok {
										fields[fk] = fs
									}
								}
							}
						}
					}
					// 构造 protobuf RecordEntry 消息
					out = append(out, &pb.RecordEntry{Fields: fields})
				}
			}
			return out
		}
	}
	return nil
}

// getRecordEntry 从 map 中提取单个 RecordEntry（protobuf 消息）。
//
// 与 getRecordEntries 类似，但仅提取单个记录而非数组。
// 典型用途：classify/record 等单记录分类接口
func getRecordEntry(m map[string]any, key string) *pb.RecordEntry {
	if v, ok := m[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			fields := make(map[string]string)
			for k, val := range mm {
				// 只处理 "fields" 键
				if k == "fields" {
					if fmap, ok := val.(map[string]any); ok {
						for fk, fv := range fmap {
							if fs, ok := fv.(string); ok {
								fields[fk] = fs
							}
						}
					}
				}
			}
			return &pb.RecordEntry{Fields: fields}
		}
	}
	return nil
}

// getBool 从 map 中安全提取布尔字段，不存在或类型不匹配时返回默认值。
//
// 典型用途：提取 mask_input（复核导出时是否脱敏输入）等布尔开关。
func getBool(m map[string]any, key string, def bool) bool {
	if v, ok := m[key]; ok {
		// 类型断言为 bool
		if b, ok := v.(bool); ok {
			return b
		}
	}
	return def
}

// getStringMapFromMap 从嵌套 map 中提取 map[string]string。
//
// 功能与 getStringMap 完全一致，保留为历史兼容。
func getStringMapFromMap(m map[string]any, key string) map[string]string {
	if v, ok := m[key]; ok {
		if mm, ok := v.(map[string]any); ok {
			out := make(map[string]string, len(mm))
			for k, val := range mm {
				if s, ok := val.(string); ok {
					out[k] = s
				}
			}
			return out
		}
	}
	return nil
}

// getDoubleChunks 从 map 中提取 DoubleChunk 列表（分块浮点数数据）。
//
// 前端发送的 JSON 格式：
//   { "chunks": [{"values": [1.0, 2.0, 3.0]}, {"values": [4.0, 5.0]}] }
//
// 每个 chunk 包含一个 values 浮点数组，用于分块 DP 计算（chunked_count/sum/mean）。
func getDoubleChunks(m map[string]any, key string) []*pb.DoubleChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.DoubleChunk, 0, len(arr))
			for _, item := range arr {
				// 每个元素应为 {"values": [...]} 格式的 map
				if mm, ok := item.(map[string]any); ok {
					// 调用 getFloats 提取 "values" 数组
					out = append(out, &pb.DoubleChunk{Values: getFloats(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// getStringChunks 从 map 中提取 StringChunk 列表（分块字符串数据）。
//
// 前端发送的 JSON 格式：
//   { "chunks": [{"values": ["a", "b"]}, {"values": ["c"]}] }
//
// 用于分块直方图计算（chunked_histogram），每个 chunk 包含分类值数组。
func getStringChunks(m map[string]any, key string) []*pb.StringChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.StringChunk, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					// 调用 getStrings 提取 "values" 字符串数组
					out = append(out, &pb.StringChunk{Values: getStrings(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// getVectorEntries 从 map 中提取 DoubleChunk 列表（向量数据）。
//
// 前端发送的 JSON 格式：
//   { "vectors": [{"values": [1.0, 2.0]}, {"values": [3.0, 4.0]}] }
//
// 功能与 getDoubleChunks 相同，但语义上用于向量类 RPC（如 DPVectorSum）。
// 每个 DoubleChunk 代表一个向量（多维浮点数组）。
func getVectorEntries(m map[string]any, key string) []*pb.DoubleChunk {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]any); ok {
			out := make([]*pb.DoubleChunk, 0, len(arr))
			for _, item := range arr {
				if mm, ok := item.(map[string]any); ok {
					out = append(out, &pb.DoubleChunk{Values: getFloats(mm, "values")})
				}
			}
			return out
		}
	}
	return nil
}

// marshalProto 将 protobuf 消息转换为 JSON 可序列化的 Go 值。
//
// 执行逻辑：
//   1. 使用 protojson.Marshal 将 protobuf 消息序列化为 JSON 字节流
//      - UseProtoNames: true 保持字段名为 protobuf 原始名称（如 "field_name" 而非 "fieldName"）
//   2. 使用 json.Unmarshal 将 JSON 字节流解析为 Go 的 any 类型
//   3. 返回 any 类型的值，可直接被 gin.JSON 序列化返回前端
//
// 为什么需要两步转换：
//   protojson 输出的是 []byte，而 gin.JSON 需要 any 类型，
//   中间经过 JSON 解析为 Go 原生类型（map/slice/string/number/bool）
func marshalProto(msg proto.Message) (any, error) {
	// 第一步：protobuf → JSON 字节流（使用原始字段名）
	b, err := protojson.MarshalOptions{UseProtoNames: true}.Marshal(msg)
	if err != nil {
		return nil, err
	}
	// 第二步：JSON 字节流 → Go 原生类型
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, err
	}
	return v, nil
}

// extractJSONField 解析 map 中指定字段的 JSON 字符串，将其替换为结构化对象。
//
// 某些 RPC 的响应中包含 "result_json" 字段，其值为 JSON 编码的字符串，
// 如 `{"result_json": "{\"label\": \"email\", \"confidence\": 0.95}"}`。
// 本函数将该字符串解析为结构化对象，使前端收到的是嵌套 JSON 而非转义字符串。
//
// 执行逻辑：
//   1. 将 v 断言为 map[string]any
//   2. 提取指定 field 的字符串值
//   3. 尝试 json.Unmarshal 解析为 any
//   4. 成功则替换原字段值，失败则保持原样
func extractJSONField(v any, field string) any {
	// 尝试将 v 断言为 map，非 map 类型直接返回
	m, ok := v.(map[string]any)
	if !ok {
		return v
	}
	// 提取目标字段的字符串值
	raw, ok := m[field].(string)
	// 字段不存在、非字符串或为空时直接返回
	if !ok || raw == "" {
		return v
	}
	// 尝试解析 JSON 字符串为结构化对象
	var parsed any
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		// 解析失败时保持原字符串不变
		return v
	}
	// 用解析后的结构化对象替换原 JSON 字符串
	m[field] = parsed
	return m
}

// ---------------------------------------------------------------------------
// Health handler —— 健康检查
// ---------------------------------------------------------------------------

// handleHealth 处理 /v1/privacy/health 路径，调用上游 agent 的 Health RPC。
//
// 执行逻辑：
//   1. 构造空的 HealthRequest（无需参数）
//   2. 调用 client.Health() 获取 agent 状态
//   3. 返回 {"status": "ok", "namespace": "default"} 格式的 map
func (m *Mapper) handleHealth(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	// 调用上游 agent 的 Health RPC，无需请求参数
	resp, err := client.Health(ctx, &pb.HealthRequest{})
	if err != nil {
		return nil, err // gRPC 调用失败，错误向上传递
	}
	// 返回 agent 状态与当前命名空间
	return map[string]string{"status": resp.Status, "namespace": resp.Namespace}, nil
}

// ---------------------------------------------------------------------------
// Masking handlers —— 数据脱敏
//
// 所有 masking handler 的共同模式：
//   1. decode(body) 解析 JSON 请求体为 map[string]any
//   2. 用 getString/getStringMap 等辅助函数提取字段，构造 protobuf 请求
//   3. 调用对应的 gRPC 方法
//   4. 将 protobuf 响应转换为 map 返回
// ---------------------------------------------------------------------------

// handleMask 处理 /v1/privacy/mask 路径，单字段脱敏。
//
// 前端发送：{"field_name": "email", "value": "alice@example.com", "context": ""}
// 执行逻辑：提取字段名、值、上下文 → 调用 Mask RPC → 返回脱敏结果
func (m *Mapper) handleMask(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	// 解析 JSON 请求体
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 MaskRequest：字段名用于识别 PII 类型，值用于脱敏，上下文可选
	resp, err := client.Mask(ctx, &pb.MaskRequest{
		FieldName: getString(v, "field_name", ""), // 字段名（如 "email"、"phone"）
		Value:     getString(v, "value", ""),      // 待脱敏的值
		Context:   getString(v, "context", ""),    // 可选上下文信息
	})
	if err != nil {
		return nil, err
	}
	// 返回脱敏后的结果（如 "a***@example.com"）
	return map[string]string{"result": resp.Result}, nil
}

// handleMaskRecord 处理 /v1/privacy/mask_record 路径，整条记录脱敏。
//
// 前端发送：{"record": {"name": "Alice", "email": "a@b.com"}, "context": ""}
// 执行逻辑：提取记录 map → 调用 MaskRecord RPC → 返回脱敏后的完整记录
func (m *Mapper) handleMaskRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 MaskRecordRequest：record 为字段名→值的映射
	resp, err := client.MaskRecord(ctx, &pb.MaskRecordRequest{
		Record:  getStringMap(v, "record"),  // 整条记录的字段映射
		Context: getString(v, "context", ""), // 可选上下文
	})
	if err != nil {
		return nil, err
	}
	// 返回脱敏后的记录（字段名→脱敏值的映射）
	return map[string]map[string]string{"result": resp.Result}, nil
}

// handleMaskBatch 处理 /v1/privacy/mask_batch 路径，批量字段脱敏。
//
// 前端发送：{"field_names": ["email","phone"], "values": ["a@b.com","123"], "context": ""}
// 执行逻辑：提取字段名数组和值数组 → 调用 MaskBatch RPC → 返回批量脱敏结果
func (m *Mapper) handleMaskBatch(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 MaskBatchRequest：字段名和值一一对应
	resp, err := client.MaskBatch(ctx, &pb.MaskBatchRequest{
		FieldNames: getStrings(v, "field_names"), // 字段名数组
		Values:     getStrings(v, "values"),      // 值数组（与字段名一一对应）
		Context:    getString(v, "context", ""),  // 可选上下文
	})
	if err != nil {
		return nil, err
	}
	// 返回脱敏结果数组
	return map[string][]string{"results": resp.Results}, nil
}

// handleMaskDataFrame 处理 /v1/privacy/mask_dataframe 路径，DataFrame 级脱敏。
//
// 前端发送：{"data": [{"fields": {"name": "Alice"}}, ...], "columns": ["name"], "context": ""}
// 执行逻辑：提取 RecordEntry 列表 + 列名 → 调用 MaskDataFrame RPC → 返回脱敏后的数据
func (m *Mapper) handleMaskDataFrame(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 MaskDataFrameRequest：data 为多行记录，columns 指定需脱敏的列
	resp, err := client.MaskDataFrame(ctx, &pb.MaskDataFrameRequest{
		Data:    getRecordEntries(v, "data"),   // 多行记录（每行包含 fields map）
		Columns: getStrings(v, "columns"),      // 需要脱敏的列名列表
		Context: getString(v, "context", ""),   // 可选上下文
	})
	if err != nil {
		return nil, err
	}
	// 返回脱敏后的 RecordEntry 列表
	return map[string][]*pb.RecordEntry{"data": resp.Data}, nil
}

// handleHash 处理 /v1/privacy/hash 路径，HMAC 哈希脱敏。
//
// 前端发送：{"value": "alice@example.com", "salt": "my-salt"}
// 执行逻辑：提取值和盐 → 调用 Hash RPC → 返回哈希结果（不可逆脱敏）
func (m *Mapper) handleHash(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 HashRequest：value 为待哈希值，salt 为 HMAC 盐
	resp, err := client.Hash(ctx, &pb.HashRequest{
		Value: getString(v, "value", ""), // 待哈希的原始值
		Salt:  getString(v, "salt", ""),  // HMAC 盐值（调用方提供）
	})
	if err != nil {
		return nil, err
	}
	// 返回 HMAC 哈希结果（不可逆脱敏）
	return map[string]string{"result": resp.Result}, nil
}

// ---------------------------------------------------------------------------
// DP helpers —— 差分隐私公共工具
// ---------------------------------------------------------------------------

// dpRequest 从 JSON body 构造通用的 DPRequest protobuf 消息。
//
// 基础 DP 统计量（count/sum/mean）共用同一个 DPRequest 结构，
// 包含 flat 字段：values（数值数组）、epsilon（隐私预算）、mechanism（噪声机制）等。
// 各字段均从 JSON body 顶层直接读取。
func dpRequest(body json.RawMessage) (*pb.DPRequest, error) {
	// 解析 JSON body 为通用 map
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	return &pb.DPRequest{
		Values:     getFloats(v, "values"),          // 待计算的数值数组
		Epsilon:    getFloat64(v, "epsilon", 1.0),   // 隐私预算 ε，默认 1.0
		Mechanism:  getString(v, "mechanism", "laplace"), // 噪声机制，默认 Laplace
		Delta:      getFloat64(v, "delta", 0.0),     // δ 参数（Gaussian 机制需要）
		ClipLower:  getFloat64(v, "clip_lower", 0.0), // 裁剪下界
		ClipUpper:  getFloat64(v, "clip_upper", 0.0), // 裁剪上界
	}, nil
}

// handleDPCount 处理 /v1/privacy/dp/count 路径，差分隐私计数。
// 执行逻辑：dpRequest 构造请求 → 调用 DPCount RPC → 返回加噪计数结果
func (m *Mapper) handleDPCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	// 使用公共 dpRequest 构造 DPRequest（count/sum/mean 共用）
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	// 调用上游 agent 的 DPCount RPC
	resp, err := client.DPCount(ctx, req)
	if err != nil {
		return nil, err
	}
	// 返回加噪后的计数值
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPSum 处理 /v1/privacy/dp/sum 路径，差分隐私求和。
// 执行逻辑：dpRequest 构造请求 → 调用 DPSum RPC → 返回加噪求和结果
func (m *Mapper) handleDPSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPSum(ctx, req)
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPMean 处理 /v1/privacy/dp/mean 路径，差分隐私均值。
// 执行逻辑：dpRequest 构造请求 → 调用 DPMean RPC → 返回加噪均值结果
func (m *Mapper) handleDPMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	req, err := dpRequest(body)
	if err != nil {
		return nil, err
	}
	resp, err := client.DPMean(ctx, req)
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPHistogram 处理 /v1/privacy/dp/histogram 路径，差分隐私直方图。
//
// 与基础 DP 统计量不同，直方图使用独立的 DPHistogramRequest，
// 需要额外的 categories 字段（分类标签列表），且 values 为字符串数组。
func (m *Mapper) handleDPHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPHistogramRequest：values 为字符串值，categories 为分类标签
	resp, err := client.DPHistogram(ctx, &pb.DPHistogramRequest{
		Values:     getStrings(v, "values"),            // 待统计的字符串值数组
		Categories: getStrings(v, "categories"),        // 分类标签列表
		Epsilon:    getFloat64(v, "epsilon", 1.0),      // 隐私预算 ε
		Mechanism:  getString(v, "mechanism", "laplace"), // 噪声机制
		Delta:      getFloat64(v, "delta", 0.0),        // δ 参数
	})
	if err != nil {
		return nil, err
	}
	// 返回加噪后的直方图（分类标签 → 加噪计数）
	return map[string]map[string]float64{"result": resp.Result}, nil
}

// handleDPNoisyCount 处理 /v1/privacy/dp/noisy_count 路径，带噪计数。
//
// 与基础 DPCount 不同，Noisy 变体接收已计算好的 true_count（而非原始 values），
// 仅负责添加噪声，适合调用方已自行计算精确计数的场景。
func (m *Mapper) handleDPNoisyCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPNoisyCountRequest：true_count 为精确计数，RPC 负责加噪
	resp, err := client.DPNoisyCount(ctx, &pb.DPNoisyCountRequest{
		TrueCount: getFloat64(v, "true_count", 0.0),  // 精确计数值
		Epsilon:   getFloat64(v, "epsilon", 1.0),      // 隐私预算 ε
		Mechanism: getString(v, "mechanism", "laplace"), // 噪声机制
		Delta:     getFloat64(v, "delta", 0.0),        // δ 参数
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPNoisySum 处理 /v1/privacy/dp/noisy_sum 路径，带噪求和。
//
// 接收已计算好的 true_sum，添加 sensitivity/clip 参数控制噪声规模。
func (m *Mapper) handleDPNoisySum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPNoisySumRequest：true_sum + sensitivity + clip 参数
	resp, err := client.DPNoisySum(ctx, &pb.DPNoisySumRequest{
		TrueSum:     getFloat64(v, "true_sum", 0.0),     // 精确求和值
		Epsilon:     getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε
		Mechanism:   getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:       getFloat64(v, "delta", 0.0),         // δ 参数
		Sensitivity: getFloat64(v, "sensitivity", 0.0),   // 敏感度（决定噪声规模）
		ClipLower:   getFloat64(v, "clip_lower", 0.0),    // 裁剪下界
		ClipUpper:   getFloat64(v, "clip_upper", 0.0),    // 裁剪上界
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPNoisyMean 处理 /v1/privacy/dp/noisy_mean 路径，带噪均值。
//
// 接收 true_sum 和 true_count，通过 min_count 防止小样本场景下的隐私泄露。
func (m *Mapper) handleDPNoisyMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPNoisyMeanRequest：通过 true_sum/true_count 计算均值并加噪
	resp, err := client.DPNoisyMean(ctx, &pb.DPNoisyMeanRequest{
		TrueSum:     getFloat64(v, "true_sum", 0.0),     // 精确求和值
		TrueCount:   getFloat64(v, "true_count", 0.0),   // 精确计数
		Epsilon:     getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε
		Mechanism:   getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:       getFloat64(v, "delta", 0.0),         // δ 参数
		Sensitivity: getFloat64(v, "sensitivity", 0.0),   // 敏感度
		ClipLower:   getFloat64(v, "clip_lower", 0.0),    // 裁剪下界
		ClipUpper:   getFloat64(v, "clip_upper", 0.0),    // 裁剪上界
		MinCount:    getFloat64(v, "min_count", 5.0),     // 最小样本数（防止小样本泄露）
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPNoisyHistogram 处理 /v1/privacy/dp/noisy_histogram 路径，带噪直方图。
//
// 接收已计算好的 true_counts（分类标签 → 精确计数），为每个桶添加噪声。
func (m *Mapper) handleDPNoisyHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 从 JSON body 中提取 true_counts map（分类标签 → 精确计数）
	trueCounts := map[string]float64{}
	if mm, ok := v["true_counts"].(map[string]any); ok {
		// 遍历 map，将 float64 值提取到 trueCounts
		for k, val := range mm {
			if n, ok := val.(float64); ok {
				trueCounts[k] = n
			}
		}
	}
	// 构造 DPNoisyHistogramRequest：true_counts 为精确直方图，RPC 负责加噪
	resp, err := client.DPNoisyHistogram(ctx, &pb.DPNoisyHistogramRequest{
		TrueCounts: trueCounts,                          // 分类标签 → 精确计数
		Epsilon:    getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε
		Mechanism:  getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:      getFloat64(v, "delta", 0.0),         // δ 参数
	})
	if err != nil {
		return nil, err
	}
	// 返回加噪后的直方图（分类标签 → 加噪计数）
	return map[string]map[string]float64{"result": resp.Result}, nil
}

// handleDPChunkedCount 处理 /v1/privacy/dp/chunked_count 路径，分块差分隐私计数。
//
// 将大数据集分成多个 chunk，每个 chunk 独立计算 DP 计数后合并，
// 适合处理超出单次查询容量限制的大规模数据。
func (m *Mapper) handleDPChunkedCount(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPChunkedCountRequest：chunks 为分块数据，每块包含 values 数组
	resp, err := client.DPChunkedCount(ctx, &pb.DPChunkedCountRequest{
		Chunks:    getDoubleChunks(v, "chunks"),        // 分块浮点数数组
		Epsilon:   getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε（在所有 chunk 间分配）
		Mechanism: getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:     getFloat64(v, "delta", 0.0),         // δ 参数
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPChunkedSum 处理 /v1/privacy/dp/chunked_sum 路径，分块差分隐私求和。
//
// 与 ChunkedCount 类似，但计算的是加噪求和，额外支持 clip_lower/clip_upper 裁剪参数。
func (m *Mapper) handleDPChunkedSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPChunkedSumRequest：chunks + 裁剪参数
	resp, err := client.DPChunkedSum(ctx, &pb.DPChunkedSumRequest{
		Chunks:    getDoubleChunks(v, "chunks"),        // 分块浮点数数组
		Epsilon:   getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε
		Mechanism: getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:     getFloat64(v, "delta", 0.0),         // δ 参数
		ClipLower: getFloat64(v, "clip_lower", 0.0),    // 裁剪下界
		ClipUpper: getFloat64(v, "clip_upper", 0.0),    // 裁剪上界
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPChunkedMean 处理 /v1/privacy/dp/chunked_mean 路径，分块差分隐私均值。
//
// 与 ChunkedSum 类似，但计算的是加噪均值，额外支持 min_count 防止小样本泄露。
func (m *Mapper) handleDPChunkedMean(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPChunkedMeanRequest：chunks + 裁剪 + 最小样本数
	resp, err := client.DPChunkedMean(ctx, &pb.DPChunkedMeanRequest{
		Chunks:    getDoubleChunks(v, "chunks"),        // 分块浮点数数组
		Epsilon:   getFloat64(v, "epsilon", 1.0),       // 隐私预算 ε
		Mechanism: getString(v, "mechanism", "laplace"),  // 噪声机制
		Delta:     getFloat64(v, "delta", 0.0),         // δ 参数
		ClipLower: getFloat64(v, "clip_lower", 0.0),    // 裁剪下界
		ClipUpper: getFloat64(v, "clip_upper", 0.0),    // 裁剪上界
		MinCount:  getFloat64(v, "min_count", 5.0),     // 最小样本数
	})
	if err != nil {
		return nil, err
	}
	return map[string]float64{"result": resp.Result}, nil
}

// handleDPChunkedHistogram 处理 /v1/privacy/dp/chunked_histogram 路径，分块差分隐私直方图。
//
// 与 ChunkedCount/Sum 不同，分块直方图使用 StringChunk（分类值）而非 DoubleChunk（浮点数）。
func (m *Mapper) handleDPChunkedHistogram(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPChunkedHistogramRequest：chunks 为字符串分块，categories 为分类标签
	resp, err := client.DPChunkedHistogram(ctx, &pb.DPChunkedHistogramRequest{
		Chunks:     getStringChunks(v, "chunks"),       // 分块字符串数组（分类值）
		Categories: getStrings(v, "categories"),        // 分类标签列表
		Epsilon:    getFloat64(v, "epsilon", 1.0),      // 隐私预算 ε
		Mechanism:  getString(v, "mechanism", "laplace"), // 噪声机制
		Delta:      getFloat64(v, "delta", 0.0),        // δ 参数
	})
	if err != nil {
		return nil, err
	}
	// 返回加噪后的直方图（分类标签 → 加噪计数）
	return map[string]map[string]float64{"result": resp.Result}, nil
}

// handleDPAggregate 处理 /v1/privacy/dp/aggregate 路径，多指标差分隐私聚合。
//
// 支持一次请求同时计算多个指标（count/sum/mean 等），
// 通过 specs_json 指定聚合规格，返回 JSON 格式的聚合结果。
func (m *Mapper) handleDPAggregate(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPAggregateRequest：rows 为数据行，specs_json 为聚合规格
	resp, err := client.DPAggregate(ctx, &pb.DPAggregateRequest{
		Rows:          getRecordEntries(v, "rows"),       // 数据行（每行包含 fields map）
		SpecsJson:     getString(v, "specs_json", "{}"),  // 聚合规格 JSON（指定哪些列、哪些指标）
		Epsilon:       getFloat64(v, "epsilon", 1.0),     // 隐私预算 ε
		Delta:         getFloat64(v, "delta", 0.0),       // δ 参数
		Mechanism:     getString(v, "mechanism", "laplace"), // 噪声机制
		ReturnDetails: true,                              // 始终返回详细信息
	})
	if err != nil {
		return nil, err
	}
	// 尝试将 results_json 解析为结构化对象，失败则保持原始字符串
	var results any
	if err := json.Unmarshal([]byte(resp.ResultsJson), &results); err != nil {
		return map[string]string{"results_json": resp.ResultsJson}, nil
	}
	return map[string]any{"results": results}, nil
}

// handleDPVectorSum 处理 /v1/privacy/dp/vector_sum 路径，差分隐私向量求和。
//
// 对多个向量进行加噪求和，适合机器学习场景下的梯度聚合。
// 通过 max_norm 限制每个向量的 L2 范数（裁剪），控制敏感度。
func (m *Mapper) handleDPVectorSum(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPVectorSumRequest：vectors 为多个向量，max_norm 为裁剪阈值
	resp, err := client.DPVectorSum(ctx, &pb.DPVectorSumRequest{
		Vectors:       getVectorEntries(v, "vectors"),     // 多个向量（每个为 DoubleChunk）
		MaxNorm:       getFloat64(v, "max_norm", 1.0),     // L2 范数裁剪阈值
		Epsilon:       getFloat64(v, "epsilon", 1.0),      // 隐私预算 ε
		Delta:         getFloat64(v, "delta", 0.0),        // δ 参数
		Mechanism:     getString(v, "mechanism", "gaussian"), // 默认使用 Gaussian 机制
		ReturnDetails: true,                               // 返回详细信息
	})
	if err != nil {
		return nil, err
	}
	// 将 ResultDetails（protobuf）转换为 JSON 可序列化格式
	resultDetails, _ := marshalProto(resp.ResultDetails)
	return map[string]any{
		"noisy_vector":    resp.NoisyVector,    // 加噪后的向量
		"result_details": resultDetails,         // 详细信息（包含噪声规模等）
	}, nil
}

// handleDPAdaptiveClip 处理 /v1/privacy/dp/adaptive_clip 路径，自适应裁剪。
//
// 通过迭代算法自动确定合适的裁剪上下界，
// 无需调用方手动指定 clip_lower/clip_upper。
// 基于目标分位数（target_quantile）和迭代次数（num_iterations）进行自适应调整。
func (m *Mapper) handleDPAdaptiveClip(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPAdaptiveClipRequest：values + 自适应参数
	resp, err := client.DPAdaptiveClip(ctx, &pb.DPAdaptiveClipRequest{
		Values:         getFloats(v, "values"),                    // 原始数值数组
		Epsilon:        getFloat64(v, "epsilon", 1.0),             // 隐私预算 ε
		TargetQuantile: getFloat64(v, "target_quantile", 0.95),    // 目标分位数（默认 95%）
		NumIterations:  getInt32(v, "num_iterations", 15),         // 迭代次数（默认 15）
		InitialClip:    getFloat64(v, "initial_clip", 10.0),       // 初始裁剪值
	})
	if err != nil {
		return nil, err
	}
	// 返回自适应算法确定的裁剪上下界
	return map[string]float64{
		"clip_lower": resp.ClipLower, // 自适应确定的裁剪下界
		"clip_upper": resp.ClipUpper, // 自适应确定的裁剪上界
	}, nil
}

// handleDPGroupBy 处理 /v1/privacy/dp/groupby 路径，分组差分隐私聚合。
//
// 按指定列分组数据，对每组独立计算差分隐私聚合（count/sum/mean）。
// 结果以 JSON 字符串返回，本函数自动解析为结构化对象。
func (m *Mapper) handleDPGroupBy(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 DPGroupByRequest：rows 为数据，group_col/target_col/agg 指定分组聚合逻辑
	resp, err := client.DPGroupBy(ctx, &pb.DPGroupByRequest{
		Rows:      getRecordEntries(v, "rows"),       // 数据行
		GroupCol:  getString(v, "group_col", ""),     // 分组列名
		TargetCol: getString(v, "target_col", ""),    // 目标列名（聚合对象）
		Agg:       getString(v, "agg", ""),           // 聚合类型（"count"/"sum"/"mean"）
		Epsilon:   getFloat64(v, "epsilon", 1.0),     // 隐私预算 ε
		Delta:     getFloat64(v, "delta", 0.0),       // δ 参数
		Mechanism: getString(v, "mechanism", "laplace"), // 噪声机制
		ClipLower: getFloat64(v, "clip_lower", 0.0),  // 裁剪下界
		ClipUpper: getFloat64(v, "clip_upper", 0.0),  // 裁剪上界
	})
	if err != nil {
		return nil, err
	}
	// 尝试将 result_json 解析为结构化对象，失败则保持原始字符串
	var result any
	if err := json.Unmarshal([]byte(resp.ResultJson), &result); err != nil {
		return map[string]string{"result_json": resp.ResultJson}, nil
	}
	return map[string]any{"result": result}, nil
}

// ---------------------------------------------------------------------------
// LDP handlers —— 本地差分隐私
//
// LDP 与 DP 的核心区别：噪声在客户端本地添加，
// 服务端永远无法获取用户的原始数据，只能根据扰动后的数据进行统计估计。
// 分为两组：
//   - perturb（扰动）：客户端本地加噪
//   - estimate（估计）：服务端根据扰动数据估计真实分布
// ---------------------------------------------------------------------------

// handlePerturbBinary 处理 /v1/privacy/ldp/perturb/binary 路径，二进制值本地扰动。
//
// 对每个 0/1 值以一定概率翻转（由 epsilon 控制），
// 使服务端无法确定单个用户的真实值。
func (m *Mapper) handlePerturbBinary(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 PerturbBinaryBatchRequest：values 为 0/1 数组
	resp, err := client.PerturbBinaryBatch(ctx, &pb.PerturbBinaryBatchRequest{
		Values:  getIntSlice(v, "values"),         // 原始 0/1 值数组
		Epsilon: getFloat64(v, "epsilon", 1.0),    // 隐私预算 ε（越小噪声越大）
	})
	if err != nil {
		return nil, err
	}
	// 返回扰动后的 0/1 值数组
	return map[string][]int32{"results": resp.Results}, nil
}

// handlePerturbCategorical 处理 /v1/privacy/ldp/perturb/categorical 路径，分类值本地扰动。
//
// 对每个分类值以一定概率替换为随机分类（随机响应机制），
// 使服务端无法确定单个用户的真实分类。
func (m *Mapper) handlePerturbCategorical(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 PerturbCategoricalBatchRequest：values 为原始分类值，categories 为所有可能分类
	resp, err := client.PerturbCategoricalBatch(ctx, &pb.PerturbCategoricalBatchRequest{
		Values:     getStrings(v, "values"),         // 原始分类值数组
		Categories: getStrings(v, "categories"),     // 所有可能的分类标签
		Epsilon:    getFloat64(v, "epsilon", 1.0),   // 隐私预算 ε
	})
	if err != nil {
		return nil, err
	}
	// 返回扰动后的分类值数组
	return map[string][]string{"results": resp.Results}, nil
}

// handleEstimateBinary 处理 /v1/privacy/ldp/estimate/binary 路径，二进制频率估计。
//
// 根据扰动后的 0/1 值数组，使用统计方法估计真实的 1 的比例。
func (m *Mapper) handleEstimateBinary(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 EstimateBinaryFrequencyRequest：reported_values 为扰动后的值
	resp, err := client.EstimateBinaryFrequency(ctx, &pb.EstimateBinaryFrequencyRequest{
		ReportedValues: getIntSlice(v, "reported_values"), // 扰动后的 0/1 值
		Epsilon:        getFloat64(v, "epsilon", 1.0),     // 隐私预算 ε（需与扰动时一致）
	})
	if err != nil {
		return nil, err
	}
	// 返回估计的真实频率（0~1 之间的浮点数）
	return map[string]float64{"estimated_frequency": resp.EstimatedFrequency}, nil
}

// handleEstimateCategorical 处理 /v1/privacy/ldp/estimate/categorical 路径，分类直方图估计。
//
// 根据扰动后的分类值数组，使用统计方法估计每个分类的真实比例。
func (m *Mapper) handleEstimateCategorical(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 EstimateCategoricalHistogramRequest：reported_values 为扰动后的值
	resp, err := client.EstimateCategoricalHistogram(ctx, &pb.EstimateCategoricalHistogramRequest{
		ReportedValues: getStrings(v, "reported_values"), // 扰动后的分类值
		Categories:     getStrings(v, "categories"),      // 所有可能的分类标签
		Epsilon:        getFloat64(v, "epsilon", 1.0),    // 隐私预算 ε
	})
	if err != nil {
		return nil, err
	}
	// 返回估计的直方图（分类标签 → 估计比例）
	return map[string]map[string]float64{"estimated_histogram": resp.EstimatedHistogram}, nil
}

// ---------------------------------------------------------------------------
// K-Anonymity handlers —— K-匿名
//
// K-匿名通过泛化准标识符（QI）使每条记录至少与 K-1 条其他记录不可区分，
// 从而防止通过准标识符组合定位到个人。
// 使用 Mondrian 算法进行表级泛化。
// ---------------------------------------------------------------------------

// handleKAnonymizeRecord 处理 /v1/privacy/k_anonymize/record 路径，单条记录 K-匿名检查。
//
// 检查给定记录在数据集中的 K-匿名度，返回泛化后的记录。
func (m *Mapper) handleKAnonymizeRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 KAnonymizeRequest：record 为待检查记录，qi_cols 为准标识符列名
	resp, err := client.KAnonymizeRecord(ctx, &pb.KAnonymizeRequest{
		Record: getStringMap(v, "record"), // 待检查的记录（字段名→值）
		QiCols: getStrings(v, "qi_cols"),  // 准标识符列名列表
		K:      getInt32(v, "k", 5),       // K 值（默认 5）
	})
	if err != nil {
		return nil, err
	}
	// 返回泛化后的记录
	return map[string]map[string]string{"result": resp.Result}, nil
}

// handleKAnonymizeTable 处理 /v1/privacy/k_anonymize/table 路径，表级 K-匿名。
//
// 使用 Mondrian 算法对整个数据集进行 K-匿名泛化。
// max_depth 控制泛化树的最大深度，防止过度泛化。
func (m *Mapper) handleKAnonymizeTable(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 KAnonymizeTableRequest：rows 为数据集，qi_cols 为准标识符
	resp, err := client.KAnonymizeTable(ctx, &pb.KAnonymizeTableRequest{
		Rows:     getRecordEntries(v, "rows"),   // 数据集（多行记录）
		QiCols:   getStrings(v, "qi_cols"),      // 准标识符列名列表
		K:        getInt32(v, "k", 5),           // K 值（默认 5）
		MaxDepth: getInt32(v, "max_depth", 10),  // 泛化树最大深度（默认 10）
	})
	if err != nil {
		return nil, err
	}
	// 返回泛化后的数据集（RecordEntry 列表）
	return map[string][]*pb.RecordEntry{"rows": resp.Rows}, nil
}

// handleKAnonymizeDataFrame 处理 /v1/privacy/k_anonymize/dataframe 路径，DataFrame 级 K-匿名。
//
// 与 handleKAnonymizeTable 功能相同，但使用 "data" 字段名而非 "rows"，
// 与 mask_dataframe 的 JSON 契约保持一致。
func (m *Mapper) handleKAnonymizeDataFrame(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 KAnonymizeDataFrameRequest：data 为数据集（字段名为 "data" 而非 "rows"）
	resp, err := client.KAnonymizeDataFrame(ctx, &pb.KAnonymizeDataFrameRequest{
		Data:     getRecordEntries(v, "data"),   // 数据集（多行记录）
		QiCols:   getStrings(v, "qi_cols"),      // 准标识符列名列表
		K:        getInt32(v, "k", 5),           // K 值（默认 5）
		MaxDepth: getInt32(v, "max_depth", 10),  // 泛化树最大深度（默认 10）
	})
	if err != nil {
		return nil, err
	}
	// 返回泛化后的数据集
	return map[string][]*pb.RecordEntry{"rows": resp.Data}, nil
}

// ---------------------------------------------------------------------------
// Query Obfuscation handlers —— 查询混淆
//
// 向真实查询中注入虚假查询（dummy queries），
// 使攻击者（如数据库管理员、网络监控者）无法区分哪些是真实查询。
// ---------------------------------------------------------------------------

// handleObfuscateQuery 处理 /v1/privacy/qol/obfuscate 路径，单条查询混淆。
//
// 在真实查询旁注入 num_dummies 个虚假查询，返回混合后的查询列表。
func (m *Mapper) handleObfuscateQuery(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ObfuscateQueryRequest：query 为真实查询，num_dummies 为虚假查询数量
	resp, err := client.ObfuscateQuery(ctx, &pb.ObfuscateQueryRequest{
		Query:        getString(v, "query", ""),              // 真实查询
		NumDummies:   getInt32(v, "num_dummies", 3),          // 虚假查询数量（默认 3）
		Domain:       getString(v, "domain", "medical"),      // 查询领域（"medical"/"generic"）
		MedicalPool:  getStrings(v, "medical_pool"),          // 自定义医疗领域虚假查询池
		GenericPool:  getStrings(v, "generic_pool"),          // 自定义通用领域虚假查询池
	})
	if err != nil {
		return nil, err
	}
	// 返回混合后的查询列表（真实 + 虚假，顺序已打乱）
	return map[string][]string{"result": resp.Result}, nil
}

// handleObfuscateQueryBatch 处理 /v1/privacy/qol/obfuscate/batch 路径，批量查询混淆。
//
// 对多条查询分别注入虚假查询，返回每条查询的混淆结果。
func (m *Mapper) handleObfuscateQueryBatch(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ObfuscateQueryBatchRequest：queries 为多条真实查询
	resp, err := client.ObfuscateQueryBatch(ctx, &pb.ObfuscateQueryBatchRequest{
		Queries:      getStrings(v, "queries"),             // 多条真实查询
		NumDummies:   getInt32(v, "num_dummies", 3),        // 每条查询的虚假查询数量
		Domain:       getString(v, "domain", "medical"),    // 查询领域
		MedicalPool:  getStrings(v, "medical_pool"),        // 自定义医疗虚假查询池
		GenericPool:  getStrings(v, "generic_pool"),        // 自定义通用虚假查询池
	})
	if err != nil {
		return nil, err
	}
	// 将 protobuf 重复消息列表展平为 [][]string
	results := make([][]string, 0, len(resp.Results))
	for _, r := range resp.Results {
		// 每个 r.Result 为一条查询的混淆结果（真实 + 虚假）
		results = append(results, r.Result)
	}
	return map[string][][]string{"results": results}, nil
}

// ---------------------------------------------------------------------------
// Classification handlers —— 数据分类（三层漏斗）
//
// 分类漏斗的三层：
//   Layer 1：规则引擎（DefaultRuleEngine）—— 基于字段名/值的快速规则匹配
//   Layer 2：小型 NER（ONNX Runtime）—— 本地命名实体识别
//   Layer 3：本地 LLM/VLM（Qwen2-VL）—— 大语言模型兜底分类
//
// 所有分类 handler 的共同模式：
//   1. decode(body) 解析 JSON
//   2. 构造 protobuf 请求，params_json 携带分类参数（模板、合规标准等）
//   3. 调用 gRPC 方法
//   4. marshalProto(resp) 将 protobuf 响应转换为 JSON map
//   5. extractJSONField(data, "result_json") 解析内嵌 JSON 字符串为结构化对象
// ---------------------------------------------------------------------------

// handleClassifyField 处理 /v1/privacy/classify/field 路径，单字段分类。
//
// 通过三层漏斗对单个字段进行分类，返回敏感级别、分类依据等。
func (m *Mapper) handleClassifyField(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ClassifyFieldRequest：field_name + value + 可选分类参数
	resp, err := client.ClassifyField(ctx, &pb.ClassifyFieldRequest{
		FieldName:  getString(v, "field_name", ""),   // 字段名（如 "email"）
		Value:      getString(v, "value", ""),         // 字段值（如 "alice@example.com"）
		ParamsJson: getString(v, "params_json", "{}"), // 分类参数 JSON（模板、合规标准等）
	})
	if err != nil {
		return nil, err
	}
	// 将 protobuf 响应转换为 JSON map，并解析内嵌的 result_json 字段
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleClassifyRecord 处理 /v1/privacy/classify/record 路径，整条记录分类。
//
// 对一条记录的所有字段进行综合分类，返回每个字段的敏感级别。
func (m *Mapper) handleClassifyRecord(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ClassifyRecordRequest：record 为字段名→值的映射
	resp, err := client.ClassifyRecord(ctx, &pb.ClassifyRecordRequest{
		Record:     getRecordEntry(v, "record"),      // 整条记录（包含 fields map）
		ParamsJson: getString(v, "params_json", "{}"), // 分类参数 JSON
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleClassifyTable 处理 /v1/privacy/classify/table 路径，表级分类（同步）。
//
// 对整个表格进行同步分类，schema 指定列名，rows 为数据行。
// 适合小表实时分类；大表建议使用异步版本。
func (m *Mapper) handleClassifyTable(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ClassifyTableRequest：schema 为列名，rows 为数据行
	resp, err := client.ClassifyTable(ctx, &pb.ClassifyTableRequest{
		Schema:     getStrings(v, "schema"),          // 列名列表
		Rows:       getRecordEntries(v, "rows"),       // 数据行
		ParamsJson: getString(v, "params_json", "{}"), // 分类参数 JSON
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleClassifyTableAsync 处理 /v1/privacy/classify/table/async 路径，表级分类（异步）。
//
// 提交异步分类任务，立即返回 job_id，后续通过
// /v1/privacy/classify/jobs/{job_id} 轮询结果。
func (m *Mapper) handleClassifyTableAsync(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ClassifyTableAsyncRequest：与同步版本参数相同
	resp, err := client.ClassifyTableAsync(ctx, &pb.ClassifyTableAsyncRequest{
		Schema:     getStrings(v, "schema"),          // 列名列表
		Rows:       getRecordEntries(v, "rows"),       // 数据行
		ParamsJson: getString(v, "params_json", "{}"), // 分类参数 JSON
	})
	if err != nil {
		return nil, err
	}
	// 返回异步任务信息（job_id 用于后续轮询）
	return map[string]string{
		"job_id":     resp.JobId,     // 异步任务唯一标识
		"status":     resp.Status,    // 任务状态（"pending"/"running"/"completed"/"failed"）
		"created_at": resp.CreatedAt, // 任务创建时间
	}, nil
}

// handleGetClassificationJob 处理 /v1/privacy/classify/jobs/{job_id} 路径，查询异步分类任务。
//
// 注意：该 handler 不通过静态分发表查找，而是由 Dispatch 中的
// 正则匹配动态路由调用，jobID 作为参数直接传入。
func (m *Mapper) handleGetClassificationJob(ctx context.Context, client pb.PrivacyServiceClient, jobID string) (any, error) {
	// 构造 GetClassificationJobRequest：仅需 job_id
	resp, err := client.GetClassificationJob(ctx, &pb.GetClassificationJobRequest{JobId: jobID})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleClassifySecretFlow 处理 /v1/privacy/classify/secretflow 路径，SecretFlow 联邦分类。
//
// 支持多方安全计算场景下的数据分类，
// party 标识当前参与方，data_json 携带本方数据。
func (m *Mapper) handleClassifySecretFlow(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ClassifySecretFlowRequest：party + params + data
	resp, err := client.ClassifySecretFlow(ctx, &pb.ClassifySecretFlowRequest{
		Party:      getString(v, "party", ""),         // 当前参与方标识
		ParamsJson: getString(v, "params_json", "{}"), // 分类参数 JSON
		DataJson:   getString(v, "data_json", "{}"),   // 本方数据 JSON
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleConfirmReview 处理 /v1/privacy/classify/review/confirm 路径，人工复核确认。
//
// 允许人工审核分类结果，可修正敏感级别并添加备注。
func (m *Mapper) handleConfirmReview(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ConfirmReviewRequest：review_id + 修正信息
	resp, err := client.ConfirmReview(ctx, &pb.ConfirmReviewRequest{
		ReviewId:       getString(v, "review_id", ""),        // 复核记录 ID
		CorrectedLevel: getString(v, "corrected_level", ""),  // 修正后的敏感级别（可选）
		Reviewer:       getString(v, "reviewer", ""),         // 审核人
		Comment:        getString(v, "comment", ""),          // 审核备注
	})
	if err != nil {
		return nil, err
	}
	data, err := marshalProto(resp)
	if err != nil {
		return nil, err
	}
	return extractJSONField(data, "result_json"), nil
}

// handleExportReviews 处理 /v1/privacy/classify/review/export 路径，复核结果导出。
//
// 支持 JSONL 格式导出，可选是否对输入数据进行脱敏。
func (m *Mapper) handleExportReviews(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 ExportReviewsRequest：format 为导出格式，mask_input 控制是否脱敏
	resp, err := client.ExportReviews(ctx, &pb.ExportReviewsRequest{
		Format:    getString(v, "format", "jsonl"),      // 导出格式（默认 JSONL）
		MaskInput: getBool(v, "mask_input", false),      // 是否对输入数据脱敏
	})
	if err != nil {
		return nil, err
	}
	// 返回导出的数据字符串
	return map[string]string{"data": resp.Data}, nil
}

// ---------------------------------------------------------------------------
// Profile handler —— 个性化配置推荐
// ---------------------------------------------------------------------------

// handleRecommendParams 处理 /v1/privacy/profile/recommend 路径，个性化隐私参数推荐。
//
// 根据数据特征（数值分布、记录内容等）自动推荐最优隐私参数，
// 如 epsilon 值、噪声机制、裁剪范围等。
func (m *Mapper) handleRecommendParams(ctx context.Context, client pb.PrivacyServiceClient, body json.RawMessage) (any, error) {
	v, err := decode(body)
	if err != nil {
		return nil, err
	}
	// 构造 RecommendRequest：namespace + 数据特征
	resp, err := client.RecommendParams(ctx, &pb.RecommendRequest{
		Namespace: getString(v, "namespace", ""),   // 预算命名空间
		Values:    getFloats(v, "values"),           // 数值数据（用于分布分析）
		Rows:      getRecordEntries(v, "rows"),       // 记录数据（用于内容分析）
		QiCols:    getStrings(v, "qi_cols"),          // 准标识符列名
	})
	if err != nil {
		return nil, err
	}
	// 尝试将推荐参数 JSON 解析为结构化对象，失败则保持原始字符串
	var recommended any
	if err := json.Unmarshal([]byte(resp.RecommendedParamsJson), &recommended); err != nil {
		recommended = resp.RecommendedParamsJson
	}
	return map[string]any{
		"status":             resp.Status,             // 推荐状态
		"namespace":          resp.Namespace,           // 预算命名空间
		"recommended_params": recommended,              // 推荐的隐私参数（结构化对象或原始字符串）
	}, nil
}

// ---------------------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------------------

// lower 将字符串转为小写并去除首尾空白。
//
// 用于大小写不敏感的参数解析（如 mechanism、format 等枚举值）。
func lower(s string) string {
	// TrimSpace 去除首尾空白，ToLower 转为小写
	return strings.ToLower(strings.TrimSpace(s))
}

// 确保 protojson 包被引用，避免未使用导入的编译错误。
// 即使所有 handler 均不使用 protojson.MarshalOptions{}，
// 该变量声明也能保证 import 不会报错。
var _ = protojson.MarshalOptions{}
