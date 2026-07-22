import { useState, useEffect, useRef } from 'react';
import type { EndpointSample, ProxyResponse, HistoryEntry } from '@/types/api';
import { proxyRequest } from '@/api/client';
import { categoryMeta } from '@/lib/categories';
import { buildCurl, deriveAgentBaseUrl } from '@/lib/curl';
import { loadHistory, addHistory, removeHistory, clearHistory } from '@/lib/history';
import { Icon } from '@/components/icons';
import ResponsePanel from '@/components/ResponsePanel';
import HistoryPanel from '@/components/HistoryPanel';

interface EndpointViewProps {
  sample: EndpointSample;
  onBack: () => void;
  /** agent REST 地址（用于生成 cURL），来自健康检查 */
  agentUrl?: string;
}

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

/**
 * 端点测试视图：上方为接口信息栏，下方左右分栏（请求编辑器 / 响应查看器）。
 * 支持 Cmd/Ctrl+Enter 快捷发送、JSON 格式化、cURL 导出与请求历史。
 */
export default function EndpointView({ sample, onBack, agentUrl }: EndpointViewProps) {
  const [path, setPath] = useState(sample.path);
  const [method, setMethod] = useState(sample.method);
  const [bodyText, setBodyText] = useState(formatJson(sample.body ?? {}));
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<ProxyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [showHistory, setShowHistory] = useState(false);
  const [curlCopied, setCurlCopied] = useState(false);

  useEffect(() => {
    setPath(sample.path);
    setMethod(sample.method);
    setBodyText(formatJson(sample.body ?? {}));
    setResponse(null);
    setError(null);
    setDuration(null);
    setShowHistory(false);
  }, [sample]);

  const handleSend = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    setDuration(null);

    const start = performance.now();
    try {
      let body: Record<string, any> | undefined;
      if (method !== 'GET' && bodyText.trim()) {
        try {
          body = JSON.parse(bodyText);
        } catch (e) {
          setError(`请求体 JSON 解析错误：${(e as Error).message}`);
          setLoading(false);
          return;
        }
      }

      const req = {
        method,
        path,
        body: body ?? null,
        raw_payload_b64: sample.rawPayloadB64 ?? null,
        content_type: sample.contentType ?? null,
      };

      const res = await proxyRequest(req);
      setResponse(res);
      setDuration(performance.now() - start);
      recordHistory(res.status);
    } catch (e) {
      setError((e as Error).message);
      setDuration(performance.now() - start);
      recordHistory(0);
    } finally {
      setLoading(false);
    }
  };

  /** 记录本次请求到历史（GET 且空请求体时跳过，减少噪音）。 */
  const recordHistory = (status: number) => {
    if (method === 'GET' && !bodyText.trim()) return;
    setHistory(addHistory({ method: sample.method, path: sample.path, body: bodyText, status }));
  };

  // Cmd/Ctrl+Enter 快捷发送
  const sendRef = useRef(handleSend);
  sendRef.current = handleSend;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        sendRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const handleLoadSample = () => {
    setPath(sample.path);
    setMethod(sample.method);
    setBodyText(formatJson(sample.body ?? {}));
    setResponse(null);
    setError(null);
  };

  /** 一键格式化 / 校验请求体 JSON。 */
  const handleFormat = () => {
    if (!bodyText.trim()) return;
    try {
      setBodyText(JSON.stringify(JSON.parse(bodyText), null, 2));
      setError(null);
    } catch (e) {
      setError(`JSON 格式错误：${(e as Error).message}`);
    }
  };

  /** 生成 cURL 命令并复制到剪贴板。 */
  const handleCopyCurl = async () => {
    const curl = buildCurl({ method, path, body: bodyText, baseUrl: deriveAgentBaseUrl(agentUrl) });
    try {
      await navigator.clipboard.writeText(curl);
      setCurlCopied(true);
      setTimeout(() => setCurlCopied(false), 1500);
    } catch {
      /* 忽略剪贴板不可用 */
    }
  };

  const meta = categoryMeta(sample.category);
  const endpointHistory = history.filter(
    (e) => e.method === sample.method && e.path === sample.path,
  );

  return (
    <div className="flex h-full flex-col">
      {/* 接口信息栏 */}
      <div className="shrink-0 border-b border-gray-200 bg-white px-5 py-3.5">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
            title="返回总览"
          >
            <Icon name="arrow-left" className="h-4 w-4" />
          </button>
          <span
            className={[
              'shrink-0 rounded-md px-2 py-1 text-xs font-bold',
              method === 'GET' ? 'bg-emerald-50 text-emerald-600' : 'bg-sky-50 text-sky-600',
            ].join(' ')}
          >
            {method}
          </span>
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-gray-200 bg-gray-50 px-2.5 py-1.5 font-mono text-sm text-gray-800 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100"
          />
          <span
            className={`hidden shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium sm:inline-flex ${meta.chip}`}
          >
            <Icon name={meta.icon} className="h-3.5 w-3.5" />
            {sample.category}
          </span>
        </div>
        <p className="mt-2 pl-10 text-sm text-gray-500">{sample.description}</p>
      </div>

      {/* 左右分栏：请求 / 响应 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 请求编辑器 */}
        <div className="relative flex w-1/2 flex-col border-r border-gray-200 bg-white">
          <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">请求体</h3>
            <div className="flex items-center gap-0.5">
              <button
                onClick={handleFormat}
                disabled={method === 'GET'}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-40"
                title="格式化 / 校验 JSON"
              >
                <Icon name="code" className="h-3.5 w-3.5" />
                格式化
              </button>
              <button
                onClick={handleCopyCurl}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                title="复制 cURL 命令"
              >
                <Icon name={curlCopied ? 'check' : 'copy'} className="h-3.5 w-3.5" />
                {curlCopied ? '已复制' : 'cURL'}
              </button>
              <button
                onClick={() => setShowHistory((v) => !v)}
                className={[
                  'inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs transition-colors',
                  showHistory
                    ? 'bg-indigo-50 text-indigo-600'
                    : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700',
                ].join(' ')}
                title="请求历史"
              >
                <Icon name="clock" className="h-3.5 w-3.5" />
                历史
              </button>
              <button
                onClick={handleLoadSample}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                title="恢复示例请求"
              >
                <Icon name="refresh" className="h-3.5 w-3.5" />
                重载示例
              </button>
            </div>
          </div>

          <div className="flex flex-1 flex-col overflow-hidden p-4">
            <textarea
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              disabled={method === 'GET'}
              spellCheck={false}
              className={[
                'flex-1 resize-none rounded-lg border border-gray-200 p-3 font-mono text-xs leading-relaxed transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-100',
                method === 'GET'
                  ? 'bg-gray-50 text-gray-400'
                  : 'bg-gray-50/50 text-gray-800 focus:border-indigo-400 focus:bg-white',
              ].join(' ')}
              placeholder={method === 'GET' ? 'GET 请求无需请求体' : '{ }'}
            />
            {sample.contentType && (
              <p className="mt-2 text-[11px] text-gray-400">
                Content-Type: {sample.contentType}（二进制载荷由后端处理）
              </p>
            )}

            <button
              onClick={handleSend}
              disabled={loading}
              className={[
                'mt-3 inline-flex h-9 items-center justify-center gap-2 rounded-lg text-sm font-medium text-white shadow-sm transition-colors',
                loading ? 'cursor-not-allowed bg-indigo-400' : 'bg-indigo-600 hover:bg-indigo-700',
              ].join(' ')}
              title="快捷键 Cmd/Ctrl + Enter"
            >
              <Icon name="send" className="h-3.5 w-3.5" />
              {loading ? '发送中…' : '发送请求'}
              <kbd className="ml-1 hidden rounded bg-indigo-500/40 px-1.5 py-0.5 text-[10px] font-normal text-indigo-100 sm:inline">
                ⌘↵
              </kbd>
            </button>
          </div>

          {/* 历史面板（覆盖请求编辑区） */}
          {showHistory && (
            <HistoryPanel
              entries={endpointHistory}
              onRestore={(body) => {
                setBodyText(body);
                setShowHistory(false);
              }}
              onDelete={(id) => setHistory(removeHistory(id))}
              onClear={() => setHistory(clearHistory())}
              onClose={() => setShowHistory(false)}
            />
          )}
        </div>

        {/* 响应查看器 */}
        <div className="flex w-1/2 flex-col bg-white">
          <ResponsePanel response={response} error={error} duration={duration} path={path} />
        </div>
      </div>
    </div>
  );
}
