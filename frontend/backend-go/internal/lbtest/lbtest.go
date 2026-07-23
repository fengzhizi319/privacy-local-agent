// Package lbtest 实现负载均衡测试的策略分发与统计逻辑。
//
// 中文说明：
// 控制台 Go 后端的 /api/lb_test 端点把探测请求按策略（round_robin / random /
// least_connections）分发到用户配置的多个 agent 后端地址，并统计各节点的
// 命中数、成功/失败数与延迟分布。探测用的 *http.Client 可注入，便于测试时
// 指向 httptest 起立的假后端。
package lbtest

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/fengzhizi319/privacy-local-agent/frontend/backend-go/internal/models"
)

// 支持的三种分发策略常量。
const (
	StrategyRoundRobin       = "round_robin"
	StrategyRandom           = "random"
	StrategyLeastConnections = "least_connections"
)

// PickBackends 按策略生成 n 个探测请求对应的后端下标序列。
//
//   - round_robin：依次轮询，分发最均匀；
//   - random：独立随机选择；
//   - least_connections：每次选当前累计命中最少的节点（同数取下标小者）。
func PickBackends(strategy string, n, numBackends int) ([]int, error) {
	if numBackends <= 0 {
		return nil, nil
	}
	switch strategy {
	case StrategyRoundRobin:
		seq := make([]int, n)
		for i := range seq {
			seq[i] = i % numBackends
		}
		return seq, nil
	case StrategyRandom:
		seq := make([]int, n)
		for i := range seq {
			seq[i] = rand.Intn(numBackends)
		}
		return seq, nil
	case StrategyLeastConnections:
		counts := make([]int, numBackends)
		seq := make([]int, 0, n)
		for i := 0; i < n; i++ {
			idx := 0
			for j := 1; j < numBackends; j++ {
				if counts[j] < counts[idx] {
					idx = j
				}
			}
			counts[idx]++
			seq = append(seq, idx)
		}
		return seq, nil
	default:
		return nil, fmt.Errorf("不支持的策略 '%s'，可选: round_robin/random/least_connections", strategy)
	}
}

// Run 执行负载均衡探测并统计各节点命中与延迟。
//
// client 可注入（测试时传入指向 httptest 服务器的客户端），为 nil 时使用
// 带 10s 超时的默认客户端。NumRequests/Strategy 缺省时分别取 10/round_robin。
func Run(ctx context.Context, req models.LbTestRequest, client *http.Client) (models.LbTestResponse, error) {
	if len(req.Backends) == 0 {
		return models.LbTestResponse{}, fmt.Errorf("backends 不能为空")
	}
	if req.NumRequests <= 0 {
		req.NumRequests = 10
	}
	if req.Strategy == "" {
		req.Strategy = StrategyRoundRobin
	}
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}

	seq, err := PickBackends(req.Strategy, req.NumRequests, len(req.Backends))
	if err != nil {
		return models.LbTestResponse{}, err
	}
	probePath := req.ProbePath
	if probePath == "" {
		probePath = "/health"
	}

	type probeResult struct {
		idx     int
		latency float64
		ok      bool
	}

	overallStart := time.Now()
	results := make([]probeResult, len(seq))
	var wg sync.WaitGroup
	for i, idx := range seq {
		wg.Add(1)
		go func(i, idx int) {
			defer wg.Done()
			backend := req.Backends[idx]
			url := strings.TrimRight(backend.URL, "/") + probePath
			start := time.Now()
			ok := probe(ctx, client, url, req.ProbeBody)
			results[i] = probeResult{
				idx:     idx,
				latency: float64(time.Since(start).Microseconds()) / 1000.0,
				ok:      ok,
			}
		}(i, idx)
	}
	wg.Wait()
	totalMs := float64(time.Since(overallStart).Microseconds()) / 1000.0

	// 按节点聚合延迟与成功/失败计数。
	latencies := make([][]float64, len(req.Backends))
	success := make([]int, len(req.Backends))
	failed := make([]int, len(req.Backends))
	for _, r := range results {
		latencies[r.idx] = append(latencies[r.idx], r.latency)
		if r.ok {
			success[r.idx]++
		} else {
			failed[r.idx]++
		}
	}

	distribution := make([]models.LbDistItem, 0, len(req.Backends))
	totalSuccess, totalFailed := 0, 0
	for i, backend := range req.Backends {
		lats := latencies[i]
		count := len(lats)
		totalSuccess += success[i]
		totalFailed += failed[i]
		item := models.LbDistItem{
			Name:    backend.Name,
			URL:     backend.URL,
			Count:   count,
			Success: success[i],
			Failed:  failed[i],
		}
		if count > 0 {
			sum, minL, maxL := 0.0, lats[0], lats[0]
			for _, l := range lats {
				sum += l
				if l < minL {
					minL = l
				}
				if l > maxL {
					maxL = l
				}
			}
			item.AvgLatencyMs = round2(sum / float64(count))
			item.MinLatencyMs = round2(minL)
			item.MaxLatencyMs = round2(maxL)
		}
		distribution = append(distribution, item)
	}

	return models.LbTestResponse{
		Strategy:     req.Strategy,
		Total:        req.NumRequests,
		Success:      totalSuccess,
		Failed:       totalFailed,
		DurationMs:   round2(totalMs),
		Distribution: distribution,
	}, nil
}

// probe 向指定 url 发送一次探测请求，返回是否成功（状态码 < 400）。
//
// body 非空时用 POST 发送该 JSON 体，否则用 GET。
func probe(ctx context.Context, client *http.Client, url string, body json.RawMessage) bool {
	var req *http.Request
	var err error
	if len(body) > 0 {
		req, err = http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
		if err != nil {
			return false
		}
		req.Header.Set("Content-Type", "application/json")
	} else {
		req, err = http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return false
		}
	}
	resp, err := client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return resp.StatusCode < 400
}

// round2 把浮点数四舍五入到小数点后两位。
func round2(f float64) float64 {
	return math.Round(f*100) / 100
}
