/**
 * 响应查看器：展示请求结果。
 *
 * 三种状态：空状态（未发送）/ 错误状态 / 成功状态；
 * 成功时对 JSON 做语法高亮，并提供复制与下载按钮。
 */
import { useState, type ReactNode } from 'react';
import type { ProxyResponse } from '@/types/api';
import { Icon } from '@/components/icons';

interface ResponsePanelProps {
  response: ProxyResponse | null;
  error: string | null;
  duration: number | null;
  /** 当前请求路径，用于下载文件命名 */
  path?: string;
}

/**
 * 轻量 JSON 语法高亮：将序列化后的 JSON 按 token 着色。
 * 不引入第三方库，满足控制台展示需求。
 */
function highlightJson(json: string): ReactNode[] {
  const tokenRegex =
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g;
  const nodes: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = tokenRegex.exec(json)) !== null) {
    if (match.index > last) {
      nodes.push(<span key={key++}>{json.slice(last, match.index)}</span>);
    }
    const token = match[0];
    let cls = 'text-orange-600'; // number
    if (/^"/.test(token)) {
      cls = /:$/.test(token) ? 'text-sky-700' : 'text-emerald-700'; // key : string
    } else if (/true|false/.test(token)) {
      cls = 'text-amber-600';
    } else if (/null/.test(token)) {
      cls = 'text-rose-500';
    }
    nodes.push(
      <span key={key++} className={cls}>
        {token}
      </span>,
    );
    last = match.index + token.length;
  }
  if (last < json.length) {
    nodes.push(<span key={key++}>{json.slice(last)}</span>);
  }
  return nodes;
}

/** 复制到剪贴板按钮，复制成功后短暂显示对勾。 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 忽略剪贴板不可用 */
    }
  };
  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
      title="复制响应"
    >
      <Icon name={copied ? 'check' : 'copy'} className="h-3.5 w-3.5" />
      {copied ? '已复制' : '复制'}
    </button>
  );
}

/** 下载响应 JSON 为文件。 */
function DownloadButton({ text, path }: { text: string; path: string }) {
  const handleDownload = () => {
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const safeName = path.replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'response';
    a.href = url;
    a.download = `${safeName}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <button
      onClick={handleDownload}
      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
      title="下载响应 JSON"
    >
      <Icon name="download" className="h-3.5 w-3.5" />
      下载
    </button>
  );
}

export default function ResponsePanel({ response, error, duration, path = 'response' }: ResponsePanelProps) {
  // 空状态
  if (!response && !error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-300">
        <Icon name="zap" className="h-10 w-10" strokeWidth={1.5} />
        <p className="text-sm text-gray-400">发送请求后在此查看响应</p>
      </div>
    );
  }

  // 错误状态
  if (error) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between border-b border-red-100 px-4 py-2.5">
          <span className="inline-flex items-center gap-2 text-sm font-semibold text-red-600">
            <Icon name="alert" className="h-4 w-4" />
            请求失败
          </span>
          {duration !== null && (
            <span className="text-xs text-gray-400">{duration.toFixed(1)} ms</span>
          )}
        </div>
        <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed text-red-600">
          {error}
        </pre>
      </div>
    );
  }

  // 成功状态
  const jsonText = JSON.stringify(response?.data, null, 2);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-semibold text-emerald-700">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            {response?.status ?? 200}
          </span>
          <span className="text-xs text-gray-400">
            {response?.duration_ms !== undefined
              ? `${response.duration_ms.toFixed(2)} ms`
              : duration !== null
                ? `${duration.toFixed(1)} ms`
                : ''}
          </span>
        </div>
        <div className="flex items-center">
          <CopyButton text={jsonText} />
          <DownloadButton text={jsonText} path={path} />
        </div>
      </div>
      <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words bg-gray-50/50 p-4 font-mono text-xs leading-relaxed text-gray-700">
        {highlightJson(jsonText)}
      </pre>
    </div>
  );
}
