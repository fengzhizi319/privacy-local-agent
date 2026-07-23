import type { IconName } from '@/components/icons';

/**
 * 分类元数据：展示顺序、图标、配色与中文描述。
 * 配色使用字面量类名，确保 Tailwind 能正确生成。
 */
export interface CategoryMeta {
  icon: IconName;
  /** 图标底色 + 前景色（用于侧边栏与概览卡片） */
  chip: string;
  /** 概览卡片顶部渐变色条 */
  accent: string;
  desc: string;
}

export const CATEGORY_ORDER = [
  'Health',
  'Masking',
  'Hash',
  'DP',
  'LDP',
  'K-Anonymity',
  'Query Obfuscation',
  'Classification',
  'Budget',
  'Profile',
] as const;

export const CATEGORY_META: Record<string, CategoryMeta> = {
  Health: {
    icon: 'activity',
    chip: 'bg-emerald-50 text-emerald-600',
    accent: 'from-emerald-400 to-teal-500',
    desc: '健康检查与就绪探针',
  },
  Masking: {
    icon: 'eye-off',
    chip: 'bg-indigo-50 text-indigo-600',
    accent: 'from-indigo-400 to-violet-500',
    desc: '字段 / 记录 / 批量数据脱敏',
  },
  Hash: {
    icon: 'hash',
    chip: 'bg-slate-100 text-slate-600',
    accent: 'from-slate-400 to-slate-600',
    desc: 'HMAC 哈希',
  },
  DP: {
    icon: 'bar-chart',
    chip: 'bg-sky-50 text-sky-600',
    accent: 'from-sky-400 to-blue-500',
    desc: '差分隐私：count / sum / mean / 直方图等',
  },
  LDP: {
    icon: 'shuffle',
    chip: 'bg-cyan-50 text-cyan-600',
    accent: 'from-cyan-400 to-sky-500',
    desc: '本地差分隐私扰动与估计',
  },
  'K-Anonymity': {
    icon: 'users',
    chip: 'bg-violet-50 text-violet-600',
    accent: 'from-violet-400 to-purple-500',
    desc: 'K-匿名泛化',
  },
  'Query Obfuscation': {
    icon: 'help',
    chip: 'bg-amber-50 text-amber-600',
    accent: 'from-amber-400 to-orange-500',
    desc: '查询混淆 / 假查询注入',
  },
  Classification: {
    icon: 'tag',
    chip: 'bg-rose-50 text-rose-600',
    accent: 'from-rose-400 to-pink-500',
    desc: '数据分类（规则 / NER / LLM）',
  },
  Budget: {
    icon: 'wallet',
    chip: 'bg-lime-50 text-lime-600',
    accent: 'from-lime-400 to-green-500',
    desc: '隐私预算查询',
  },
  Profile: {
    icon: 'sliders',
    chip: 'bg-fuchsia-50 text-fuchsia-600',
    accent: 'from-fuchsia-400 to-pink-500',
    desc: '隐私参数推荐',
  },
};

/** 未在元数据中声明的分类使用的兜底配置。 */
export const FALLBACK_META: CategoryMeta = {
  icon: 'inbox',
  chip: 'bg-gray-100 text-gray-600',
  accent: 'from-gray-400 to-gray-500',
  desc: '其他接口',
};

export function categoryMeta(name: string): CategoryMeta {
  return CATEGORY_META[name] ?? FALLBACK_META;
}

/** 按预定义顺序排列分类，未知分类追加在后。 */
export function orderCategories(present: string[]): string[] {
  const ordered = CATEGORY_ORDER.filter((c) => present.includes(c)) as unknown as string[];
  for (const c of present) {
    if (!ordered.includes(c)) ordered.push(c);
  }
  return ordered;
}
