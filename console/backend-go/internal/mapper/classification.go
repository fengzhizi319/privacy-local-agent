package mapper

import (
	"context"
	"encoding/json"

	pb "github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/proto"
)

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
