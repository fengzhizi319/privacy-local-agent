// Package samples provides minimal, deterministic request payloads for every
// gRPC method exposed by privacy-local-agent.
//
// 中文说明：
// 这些示例数据直接对应 gRPC 请求消息的结构（而非 REST 的 params 包装风格）。
// 前端加载后，Go 后端将 JSON 转换为 protobuf 消息并调用对应 RPC。
package samples

import (
	"encoding/json"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/models"
)

// raw is a small helper that converts a string literal to json.RawMessage.
func raw(s string) json.RawMessage {
	return json.RawMessage(s)
}

// List returns all gRPC-supported endpoint samples.
//
// 注意：以下端点仅在 REST 中定义，未包含在 gRPC proto 中，因此不在 Go 后端支持范围内：
//   - /livez, /readyz, /readyz/llm
//   - /v1/privacy/dp/arrow_ipc
//   - /v1/privacy/budget
func List() []models.EndpointSample {
	return []models.EndpointSample{
		// Health
		{
			Method: "POST", Path: "/v1/privacy/health", Label: "Health", Category: "Health",
			Description: "gRPC 健康检查", Body: raw(`{}`), Backend: "grpc",
		},

		// Masking
		{
			Method: "POST", Path: "/v1/privacy/mask", Label: "Mask", Category: "Masking",
			Description: "单字段脱敏",
			Body: raw(`{"field_name":"email","value":"alice@example.com","context":""}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/mask_record", Label: "Mask Record", Category: "Masking",
			Description: "整条记录脱敏",
			Body: raw(`{"record":{"email":"alice@example.com","phone":"13800138000","name":"Alice","id_card":"11010119900101XXXX"},"context":""}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/mask_batch", Label: "Mask Batch", Category: "Masking",
			Description: "批量字段脱敏",
			Body: raw(`{"field_names":["email","phone","name"],"values":["bob@example.com","13900139000","Bob"],"context":""}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/mask_dataframe", Label: "Mask DataFrame", Category: "Masking",
			Description: "DataFrame 脱敏",
			Body: raw(`{"data":[{"fields":{"email":"alice@example.com","phone":"13800138000"}},{"fields":{"email":"bob@example.com","phone":"13900139000"}}],"columns":["email","phone"],"context":""}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/hash", Label: "Hash", Category: "Hash",
			Description: "HMAC 哈希",
			Body: raw(`{"value":"sensitive-value","salt":"demo-salt"}`), Backend: "grpc",
		},

		// DP
		{
			Method: "POST", Path: "/v1/privacy/dp/count", Label: "DP Count", Category: "DP",
			Description: "差分隐私计数",
			Body: raw(`{"values":[1.0,2.0,3.0,4.0,5.0],"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/sum", Label: "DP Sum", Category: "DP",
			Description: "差分隐私求和",
			Body: raw(`{"values":[1000.0,2000.0,3000.0,4000.0,5000.0],"epsilon":0.1,"mechanism":"laplace","clip_lower":0.0,"clip_upper":10000.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/mean", Label: "DP Mean", Category: "DP",
			Description: "差分隐私均值",
			Body: raw(`{"values":[20.0,30.0,40.0,50.0,60.0],"epsilon":0.1,"mechanism":"laplace","clip_lower":0.0,"clip_upper":100.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/histogram", Label: "DP Histogram", Category: "DP",
			Description: "差分隐私直方图",
			Body: raw(`{"values":["eng","hr","eng","sales","eng"],"categories":["eng","hr","sales","marketing"],"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/noisy_count", Label: "Noisy Count", Category: "DP",
			Description: "对已聚合计数加噪",
			Body: raw(`{"true_count":100.0,"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/noisy_sum", Label: "Noisy Sum", Category: "DP",
			Description: "对已聚合求和加噪",
			Body: raw(`{"true_sum":10000.0,"epsilon":0.1,"mechanism":"laplace","sensitivity":10000.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/noisy_mean", Label: "Noisy Mean", Category: "DP",
			Description: "对已聚合均值加噪",
			Body: raw(`{"true_sum":10000.0,"true_count":100.0,"epsilon":0.1,"mechanism":"laplace","sensitivity":10000.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/noisy_histogram", Label: "Noisy Histogram", Category: "DP",
			Description: "对已聚合直方图加噪",
			Body: raw(`{"true_counts":{"eng":50.0,"hr":20.0,"sales":30.0},"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/chunked_count", Label: "Chunked Count", Category: "DP",
			Description: "分块流式 DP 计数",
			Body: raw(`{"chunks":[{"values":[1.0,2.0]},{"values":[3.0,4.0]},{"values":[5.0,6.0]}],"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/chunked_sum", Label: "Chunked Sum", Category: "DP",
			Description: "分块流式 DP 求和",
			Body: raw(`{"chunks":[{"values":[1.0,2.0]},{"values":[3.0,4.0]},{"values":[5.0,6.0]}],"epsilon":0.1,"mechanism":"laplace","clip_lower":0.0,"clip_upper":10.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/chunked_mean", Label: "Chunked Mean", Category: "DP",
			Description: "分块流式 DP 均值",
			Body: raw(`{"chunks":[{"values":[1.0,2.0]},{"values":[3.0,4.0]},{"values":[5.0,6.0]}],"epsilon":0.1,"mechanism":"laplace","clip_lower":0.0,"clip_upper":10.0,"min_count":5.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/chunked_histogram", Label: "Chunked Histogram", Category: "DP",
			Description: "分块流式 DP 直方图",
			Body: raw(`{"chunks":[{"values":["eng","hr"]},{"values":["eng","sales"]},{"values":["eng","marketing"]}],"categories":["eng","hr","sales","marketing"],"epsilon":0.1,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/aggregate", Label: "DP Aggregate", Category: "DP",
			Description: "表格级原位 DP 聚合",
			Body: raw(`{"rows":[{"fields":{"age":"20","salary":"1000.0","dept":"eng"}},{"fields":{"age":"30","salary":"2000.0","dept":"hr"}},{"fields":{"age":"40","salary":"3000.0","dept":"eng"}},{"fields":{"age":"50","salary":"4000.0","dept":"sales"}}],"specs_json":"{\"age\":[\"mean\",{\"clip_lower\":0,\"clip_upper\":100}],\"salary\":[\"sum\",{\"clip_lower\":0,\"clip_upper\":10000}],\"dept\":[\"histogram\",{\"categories\":[\"eng\",\"hr\",\"sales\"]}]}","epsilon":0.5,"delta":0.0,"mechanism":"laplace"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/vector_sum", Label: "DP Vector Sum", Category: "DP",
			Description: "高维向量 DP 求和",
			Body: raw(`{"vectors":[{"values":[1.0,2.0]},{"values":[3.0,4.0]},{"values":[5.0,6.0]}],"max_norm":10.0,"epsilon":0.1,"delta":0.00001,"mechanism":"gaussian"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/adaptive_clip", Label: "Adaptive Clip", Category: "DP",
			Description: "自适应二分搜索估计截断上下界",
			Body: raw(`{"values":[1.0,5.0,10.0,15.0,20.0],"epsilon":0.1,"target_quantile":0.95,"num_iterations":15,"initial_clip":10.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/dp/groupby", Label: "DP GroupBy", Category: "DP",
			Description: "Tau-Thresholding 差分隐私 Group-By",
			Body: raw(`{"rows":[{"fields":{"dept":"eng","salary":"1000.0"}},{"fields":{"dept":"hr","salary":"2000.0"}},{"fields":{"dept":"eng","salary":"3000.0"}},{"fields":{"dept":"sales","salary":"4000.0"}}],"group_col":"dept","target_col":"salary","agg":"sum","epsilon":0.1,"delta":0.00001,"mechanism":"laplace","clip_lower":0.0,"clip_upper":10000.0}`), Backend: "grpc",
		},

		// LDP
		{
			Method: "POST", Path: "/v1/privacy/ldp/perturb/binary", Label: "Perturb Binary", Category: "LDP",
			Description: "二值本地 DP 扰动",
			Body: raw(`{"values":[0,1,1,0,1],"epsilon":1.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/ldp/perturb/categorical", Label: "Perturb Categorical", Category: "LDP",
			Description: "类别型本地 DP 扰动",
			Body: raw(`{"values":["eng","hr","eng","sales"],"categories":["eng","hr","sales"],"epsilon":1.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/ldp/estimate/binary", Label: "Estimate Binary", Category: "LDP",
			Description: "二值本地 DP 估计",
			Body: raw(`{"reported_values":[0,1,1,0,1],"epsilon":1.0}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/ldp/estimate/categorical", Label: "Estimate Categorical", Category: "LDP",
			Description: "类别型本地 DP 估计",
			Body: raw(`{"reported_values":["eng","hr","eng","sales"],"categories":["eng","hr","sales"],"epsilon":1.0}`), Backend: "grpc",
		},

		// K-Anonymity
		{
			Method: "POST", Path: "/v1/privacy/k_anonymize/record", Label: "K-Anonymize Record", Category: "K-Anonymity",
			Description: "单条记录 K-匿名泛化",
			Body: raw(`{"record":{"age":"30","zip":"100000","gender":"F"},"qi_cols":["age","zip","gender"],"k":2}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/k_anonymize/table", Label: "K-Anonymize Table", Category: "K-Anonymity",
			Description: "整张表 K-匿名泛化",
			Body: raw(`{"rows":[{"fields":{"age":"30","zip":"100000","gender":"F"}},{"fields":{"age":"31","zip":"100001","gender":"F"}},{"fields":{"age":"32","zip":"100002","gender":"M"}},{"fields":{"age":"33","zip":"100003","gender":"M"}}],"qi_cols":["age","zip","gender"],"k":2,"max_depth":10}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/k_anonymize/dataframe", Label: "K-Anonymize DataFrame", Category: "K-Anonymity",
			Description: "DataFrame K-匿名泛化",
			Body: raw(`{"data":[{"fields":{"age":"30","zip":"100000","gender":"F"}},{"fields":{"age":"31","zip":"100001","gender":"F"}},{"fields":{"age":"32","zip":"100002","gender":"M"}},{"fields":{"age":"33","zip":"100003","gender":"M"}}],"qi_cols":["age","zip","gender"],"k":2,"max_depth":10}`), Backend: "grpc",
		},

		// Query Obfuscation
		{
			Method: "POST", Path: "/v1/privacy/qol/obfuscate", Label: "Obfuscate Query", Category: "Query Obfuscation",
			Description: "查询混淆",
			Body: raw(`{"query":"糖尿病患者用药推荐","num_dummies":3,"domain":"medical"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/qol/obfuscate/batch", Label: "Obfuscate Batch", Category: "Query Obfuscation",
			Description: "批量查询混淆",
			Body: raw(`{"queries":["糖尿病患者用药推荐","高血压患者饮食建议"],"num_dummies":3,"domain":"medical"}`), Backend: "grpc",
		},

		// Classification
		{
			Method: "POST", Path: "/v1/privacy/classify/field", Label: "Classify Field", Category: "Classification",
			Description: "单字段分类",
			Body: raw(`{"field_name":"email","value":"alice@example.com","params_json":"{}"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/record", Label: "Classify Record", Category: "Classification",
			Description: "单条记录分类",
			Body: raw(`{"record":{"fields":{"email":"alice@example.com","phone":"13800138000","name":"Alice"}},"params_json":"{}"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/table", Label: "Classify Table", Category: "Classification",
			Description: "整张表分类",
			Body: raw(`{"schema":["email","phone","salary"],"rows":[{"fields":{"email":"alice@example.com","phone":"13800138000","salary":"1000"}},{"fields":{"email":"bob@example.com","phone":"13900139000","salary":"2000"}}],"params_json":"{}"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/table/async", Label: "Classify Table Async", Category: "Classification",
			Description: "异步表分类",
			Body: raw(`{"schema":["email","phone"],"rows":[{"fields":{"email":"alice@example.com","phone":"13800138000"}},{"fields":{"email":"bob@example.com","phone":"13900139000"}}],"params_json":"{}"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/secretflow", Label: "Classify SecretFlow", Category: "Classification",
			Description: "SecretFlow 数据结构分类",
			Body: raw(`{"party":"alice","params_json":"{}","data_json":"{\"schema\":[\"email\",\"phone\"],\"rows\":[{\"email\":\"alice@example.com\",\"phone\":\"13800138000\"}]}"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/review/confirm", Label: "Confirm Review", Category: "Classification",
			Description: "确认复核结果（示例 review_id 可能不存在）",
			Body: raw(`{"review_id":"demo-review-id","corrected_level":"2","reviewer":"tester","comment":"confirmed"}`), Backend: "grpc",
		},
		{
			Method: "POST", Path: "/v1/privacy/classify/review/export", Label: "Export Reviews", Category: "Classification",
			Description: "导出复核样本",
			Body: raw(`{"format":"jsonl","mask_input":false}`), Backend: "grpc",
		},

		// Profile
		{
			Method: "POST", Path: "/v1/privacy/profile/recommend", Label: "Recommend Params", Category: "Profile",
			Description: "自动推荐隐私参数",
			Body: raw(`{"namespace":"demo-recommend","values":[1.0,2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0,10.0],"rows":[{"fields":{"age":"30","zip":"100000","gender":"F"}},{"fields":{"age":"31","zip":"100001","gender":"M"}}],"qi_cols":["age","zip","gender"]}`), Backend: "grpc",
		},
	}
}
