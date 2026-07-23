/**
 * 分类分级「文本 / 图片病例」输入面板。
 *
 * 嵌入 ``EndpointView`` 的请求编辑区（仅 ``/v1/privacy/classify/field`` 端点），
 * 帮助用户无需手工准备数据即可体验多模态分类：
 *   - 文本病例：点选预置医疗文本，直接填入请求体 ``value``；
 *   - 图片病例：点选预生成病例图片或上传本地图片，读取为 base64 data URI
 *     后填入 ``value``，由后端 Qwen2-VL 执行 OCR 与敏感定级。
 *
 * 面板通过 ``onApply`` 回调把最终 ``value`` 交还给父组件写入请求体，
 * 父组件负责序列化与发送，职责保持单一。
 */
import { useRef, useState } from 'react';
import { Icon } from '@/components/icons';
import {
  TEXT_CASES,
  IMAGE_CASES,
  LEVEL_BADGE,
  type SensitivityLevel,
} from '@/lib/medicalCases';

interface ClassifyCasePanelProps {
  /** 把选中的 value 写入请求体；isImage 标识是否为图片 data URI */
  onApply: (value: string, isImage: boolean) => void;
}

type Tab = 'text' | 'image';

/** 把可 fetch 的资源 URL 读取为 base64 data URI。 */
async function urlToDataUri(url: string): Promise<string> {
  const res = await fetch(url);
  const blob = await res.blob();
  return blobToDataUri(blob);
}

/** 把 File / Blob 读取为 base64 data URI。 */
function blobToDataUri(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** 敏感等级徽章。 */
function LevelBadge({ level }: { level: SensitivityLevel }) {
  return (
    <span
      className={`shrink-0 rounded border px-1 py-0.5 text-[10px] font-semibold ${LEVEL_BADGE[level]}`}
    >
      {level}
    </span>
  );
}

export default function ClassifyCasePanel({ onApply }: ClassifyCasePanelProps) {
  const [tab, setTab] = useState<Tab>('text');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  /** 选择文本病例：直接回填纯文本。 */
  const handleText = (id: string, text: string) => {
    setError(null);
    setActiveId(id);
    onApply(text, false);
  };

  /** 选择预生成图片病例：读取为 data URI 后回填。 */
  const handleImage = async (id: string, url: string) => {
    setBusy(true);
    setError(null);
    try {
      const dataUri = await urlToDataUri(url);
      setActiveId(id);
      onApply(dataUri, true);
    } catch (e) {
      setError(`读取图片失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  /** 上传本地图片：读取为 data URI 后回填。 */
  const handleUpload = async (file: File) => {
    if (!file.type.startsWith('image/')) {
      setError('请选择图片文件（PNG / JPG 等）');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const dataUri = await blobToDataUri(file);
      setActiveId('upload');
      onApply(dataUri, true);
    } catch (e) {
      setError(`读取图片失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-3 shrink-0 rounded-lg border border-gray-200 bg-gray-50/60">
      {/* 标题 + Tab 切换 */}
      <div className="flex items-center justify-between border-b border-gray-200 px-3 py-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-600">
          <Icon name="tag" className="h-3.5 w-3.5 text-rose-500" />
          文本 / 图片病例测试
        </span>
        <div className="flex overflow-hidden rounded-md border border-gray-200 text-xs">
          {(['text', 'image'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                setError(null);
              }}
              className={[
                'px-2.5 py-1 transition-colors',
                tab === t
                  ? 'bg-indigo-600 text-white'
                  : 'bg-white text-gray-500 hover:bg-gray-100',
              ].join(' ')}
            >
              {t === 'text' ? '文本病例' : '图片病例'}
            </button>
          ))}
        </div>
      </div>

      <div className="max-h-60 overflow-y-auto p-3">
        {/* 文本病例 */}
        {tab === 'text' && (
          <div className="flex flex-wrap gap-1.5">
            {TEXT_CASES.map((c) => (
              <button
                key={c.id}
                onClick={() => handleText(c.id, c.text)}
                title={c.text}
                className={[
                  'inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors',
                  activeId === c.id
                    ? 'border-indigo-400 bg-indigo-50 text-indigo-700'
                    : 'border-gray-200 bg-white text-gray-600 hover:border-indigo-300 hover:bg-indigo-50/50',
                ].join(' ')}
              >
                <LevelBadge level={c.level} />
                {c.label}
              </button>
            ))}
          </div>
        )}

        {/* 图片病例 */}
        {tab === 'image' && (
          <div>
            <div className="grid grid-cols-3 gap-2">
              {IMAGE_CASES.map((c) => (
                <button
                  key={c.id}
                  disabled={busy}
                  onClick={() => handleImage(c.id, c.url)}
                  title={c.desc}
                  className={[
                    'group flex flex-col overflow-hidden rounded-md border bg-white text-left transition-colors disabled:opacity-50',
                    activeId === c.id
                      ? 'border-indigo-400 ring-2 ring-indigo-100'
                      : 'border-gray-200 hover:border-indigo-300',
                  ].join(' ')}
                >
                  <img
                    src={c.url}
                    alt={c.label}
                    className="h-20 w-full object-cover object-top"
                    loading="lazy"
                  />
                  <div className="flex items-center gap-1 px-1.5 py-1">
                    <LevelBadge level={c.level} />
                    <span className="truncate text-[11px] text-gray-600">{c.label}</span>
                  </div>
                </button>
              ))}
            </div>

            {/* 上传本地图片 */}
            <div className="mt-2 flex items-center gap-2">
              <button
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="inline-flex items-center gap-1 rounded-md border border-dashed border-gray-300 bg-white px-2.5 py-1.5 text-xs text-gray-600 transition-colors hover:border-indigo-400 hover:text-indigo-600 disabled:opacity-50"
              >
                <Icon name="upload" className="h-3.5 w-3.5" />
                {busy ? '读取中…' : '上传本地图片'}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUpload(f);
                  e.target.value = '';
                }}
              />
              <span className="text-[11px] text-gray-400">
                图片将转为 base64 由多模态大模型（Qwen2-VL）识别
              </span>
            </div>
          </div>
        )}

        {error && (
          <p className="mt-2 inline-flex items-center gap-1 text-xs text-rose-600">
            <Icon name="alert" className="h-3.5 w-3.5" />
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
