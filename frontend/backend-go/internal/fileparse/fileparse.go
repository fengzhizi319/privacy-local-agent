// Package fileparse 把上传的 CSV/JSON 数据文件解析为统一的 records + schema 结构。
//
// 中文说明：
// 控制台 Go 后端的 /api/upload 端点收到前端上传的文件后，用本包解析为
// []map[string]string（每条记录，值统一为字符串）与 []string（列名顺序），
// 以便进一步构造 gRPC 的 RecordEntry（其 Fields 即 map[string]string）。
// 值统一转字符串是为了与 agent 的 records 接口语义保持一致。
package fileparse

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
)

// ParseCSV 把 CSV 字节解析为 records 与 schema。
//
// 首行视为表头（schema），其余行按表头列名映射为 record；
// 某行字段数不足时以空字符串补齐，允许各行字段数不一致。
func ParseCSV(data []byte) ([]map[string]string, []string, error) {
	reader := csv.NewReader(bytes.NewReader(data))
	// 允许各行字段数与表头不一致，缺失字段以空串补齐。
	reader.FieldsPerRecord = -1
	rows, err := reader.ReadAll()
	if err != nil {
		return nil, nil, fmt.Errorf("CSV 解析失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil, fmt.Errorf("CSV 文件为空")
	}

	schema := rows[0]
	records := make([]map[string]string, 0, len(rows)-1)
	for _, row := range rows[1:] {
		record := make(map[string]string, len(schema))
		for i, col := range schema {
			if i < len(row) {
				record[col] = row[i]
			} else {
				record[col] = ""
			}
		}
		records = append(records, record)
	}
	return records, schema, nil
}

// ParseJSON 把 JSON 记录数组（list of objects）解析为 records 与 schema。
//
// schema 取所有记录中出现过的键并按字母序排序，保证结果确定（Go map 遍历无序）；
// 每个值统一转换为字符串（数字、布尔、null、嵌套对象等均有对应处理）。
func ParseJSON(data []byte) ([]map[string]string, []string, error) {
	var raw []map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, nil, fmt.Errorf("JSON 解析失败（需为记录数组）: %w", err)
	}

	seen := make(map[string]bool)
	for _, obj := range raw {
		for k := range obj {
			seen[k] = true
		}
	}
	schema := make([]string, 0, len(seen))
	for k := range seen {
		schema = append(schema, k)
	}
	sort.Strings(schema)

	records := make([]map[string]string, 0, len(raw))
	for _, obj := range raw {
		record := make(map[string]string, len(obj))
		for k, v := range obj {
			record[k] = toString(v)
		}
		records = append(records, record)
	}
	return records, schema, nil
}

// toString 把任意 JSON 值统一转换为字符串表示。
func toString(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(t)
	case nil:
		return ""
	default:
		// 嵌套对象 / 数组：序列化为紧凑 JSON 字符串。
		b, _ := json.Marshal(t)
		return string(b)
	}
}
