// Package mapper 是 REST → gRPC 路由映射的核心模块。
//
// 职责：
//   - 维护一张 "REST 路径 → gRPC handler" 的分发表（dispatch table）
//   - 接收前端发来的 JSON 请求体，解析为对应的 protobuf 消息
//   - 调用上游 privacy-local-agent 的 gRPC 方法
//   - 将 protobuf 响应转换为前端可消费的 JSON 数据结构
//
// 设计原则：
//  1. 前端使用统一的 JSON 契约（POST /api/proxy）发送请求
//  2. 本包根据 path 字段识别应调用的 gRPC RPC
//  3. 将 JSON body 转换为对应的 protobuf 请求消息
//  4. 调用 gRPC 客户端获取响应
//  5. 将 protobuf 响应转换为前端可展示的 JSON 数据
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

	// pb：由 proto/privacy.proto 生成的 gRPC 代码，
	// 包含所有 RPC 方法定义（PrivacyServiceClient）和消息类型（各种 Request/Response）
	pb "github.com/fengzhizi319/privacy-local-agent/console/backend-go/proto"
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
//  1. 初始化 jobRE 正则，用于匹配异步分类任务的动态路径
//  2. 构建 handlers 分发表，将每个固定路径绑定到对应的 handler 方法
//  3. 返回就绪的 Mapper 实例
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
		"/v1/privacy/dp/count":     m.handleDPCount,     // 差分隐私计数
		"/v1/privacy/dp/sum":       m.handleDPSum,       // 差分隐私求和
		"/v1/privacy/dp/mean":      m.handleDPMean,      // 差分隐私均值
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
		"/v1/privacy/dp/aggregate":     m.handleDPAggregate,    // 多指标聚合
		"/v1/privacy/dp/vector_sum":    m.handleDPVectorSum,    // 向量求和
		"/v1/privacy/dp/adaptive_clip": m.handleDPAdaptiveClip, // 自适应裁剪
		"/v1/privacy/dp/groupby":       m.handleDPGroupBy,      // 分组聚合

		// ── LDP（本地差分隐私）────────────────────────────────────
		// 扰动：客户端本地加噪后上报，服务端无法获取原始值
		"/v1/privacy/ldp/perturb/binary":      m.handlePerturbBinary,      // 二进制值扰动（0/1 翻转）
		"/v1/privacy/ldp/perturb/categorical": m.handlePerturbCategorical, // 分类值扰动（随机响应）
		// 估计：服务端根据扰动后的数据估计真实分布
		"/v1/privacy/ldp/estimate/binary":      m.handleEstimateBinary,      // 二进制频率估计
		"/v1/privacy/ldp/estimate/categorical": m.handleEstimateCategorical, // 分类直方图估计

		// ── K-Anonymity（K-匿名）──────────────────────────────────
		// 通过泛化准标识符（QI）使每条记录至少与 K-1 条其他记录不可区分
		"/v1/privacy/k_anonymize/record":    m.handleKAnonymizeRecord,    // 单条记录的 K-匿名检查
		"/v1/privacy/k_anonymize/table":     m.handleKAnonymizeTable,     // 表级 K-匿名（Mondrian 算法）
		"/v1/privacy/k_anonymize/dataframe": m.handleKAnonymizeDataFrame, // DataFrame 级 K-匿名

		// ── Query Obfuscation（查询混淆）──────────────────────────
		// 向真实查询中注入虚假查询（dummy queries），使攻击者无法区分真实意图
		"/v1/privacy/qol/obfuscate":       m.handleObfuscateQuery,      // 单条查询混淆
		"/v1/privacy/qol/obfuscate/batch": m.handleObfuscateQueryBatch, // 批量查询混淆

		// ── Classification（数据分类）─────────────────────────────
		// 三层分类漏斗：规则引擎 → 小型 NER → 本地 LLM/VLM
		"/v1/privacy/classify/field":          m.handleClassifyField,      // 单字段分类
		"/v1/privacy/classify/record":         m.handleClassifyRecord,     // 整条记录分类
		"/v1/privacy/classify/table":          m.handleClassifyTable,      // 表级分类（同步）
		"/v1/privacy/classify/table/async":    m.handleClassifyTableAsync, // 表级分类（异步，返回 job_id）
		"/v1/privacy/classify/secretflow":     m.handleClassifySecretFlow, // SecretFlow 联邦分类
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
//  1. 先在静态分发表 handlers 中查找精确匹配的 path
//  2. 若未命中，尝试用 jobRE 正则匹配动态路径 /v1/privacy/classify/jobs/{job_id}
//  3. 若仍未命中，返回 "unsupported gRPC path" 错误
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
