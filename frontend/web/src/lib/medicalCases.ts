/**
 * 分类分级「文本 / 图片病例」测试样例目录。
 *
 * 本模块为前端测试控制台提供两类输入样例，供 ``ClassifyCasePanel`` 使用，
 * 用于验证 ``/v1/privacy/classify/field`` 的多模态（文本 + 图片）分类能力：
 *   - 文本病例：覆盖 L3~L5 的典型医疗文本片段；
 *   - 图片病例：由 ``scripts/gen_medical_images.py`` 预生成的中文病例图片，
 *     经 Vite ``import.meta.glob`` 打包为静态资源（构建后落入 /assets，
 *     可被 Go 后端的静态托管正确服务）。
 *
 * 图片在发送前由组件读取并转换为 base64 data URI，作为 ``value`` 注入请求体；
 * 后端 LLM 层（Qwen2-VL）的 ``_detect_image`` 会识别 data URI 并执行 OCR + 定级。
 */

/** 敏感等级标签（用于徽章配色与说明）。 */
export type SensitivityLevel = 'L3' | 'L4' | 'L5';

/** 各敏感等级对应的徽章样式（字面量类名，确保 Tailwind 生成）。 */
export const LEVEL_BADGE: Record<SensitivityLevel, string> = {
  L3: 'bg-sky-50 text-sky-600 border-sky-200',
  L4: 'bg-amber-50 text-amber-600 border-amber-200',
  L5: 'bg-rose-50 text-rose-600 border-rose-200',
};

/** 文本病例样例。 */
export interface TextCase {
  id: string;
  label: string;
  level: SensitivityLevel;
  /** 填入请求体 value 的纯文本内容 */
  text: string;
}

/** 图片病例样例。 */
export interface ImageCase {
  id: string;
  label: string;
  level: SensitivityLevel;
  desc: string;
  /** Vite 打包后的图片 URL */
  url: string;
}

/**
 * 文本病例样例集（L3~L5）。
 * 内容均为虚构测试数据，仅用于演示分类分级效果。
 */
export const TEXT_CASES: TextCase[] = [
  {
    id: 'txt_clinic',
    label: '门诊病历文本',
    level: 'L3',
    text: '患者李娜，女，34岁，手机号13900002222。主诉：咳嗽咽痛3天伴低热。诊断：急性上呼吸道感染。',
  },
  {
    id: 'txt_lab',
    label: '血常规检验文本',
    level: 'L3',
    text: '血常规检验报告：白细胞6.8×10^9/L，血红蛋白152g/L，血小板210×10^9/L，各项指标均在参考范围内。',
  },
  {
    id: 'txt_discharge',
    label: '住院出院小结文本',
    level: 'L4',
    text: '住院出院小结：患者王强，住院号ZY20240512077，入院诊断2型糖尿病伴酮症酸中毒，经胰岛素强化治疗后好转出院。',
  },
  {
    id: 'txt_psych',
    label: '精神科病历文本',
    level: 'L4',
    text: '精神科病历：患者存在评论性幻听，情感淡漠，自知力缺乏，诊断为精神分裂症，予抗精神病药物系统治疗。',
  },
  {
    id: 'txt_gene',
    label: '基因检测报告文本',
    level: 'L5',
    text: '基因检测报告：检出BRCA1基因c.5266dupC致病性突变，TP53未检出突变，提示遗传性乳腺癌-卵巢癌综合征风险升高。',
  },
  {
    id: 'txt_hiv',
    label: '传染病检验文本',
    level: 'L5',
    text: '感染免疫检验：HIV抗体初筛阳性，确证试验阳性，CD4+T淋巴细胞320/μL，梅毒螺旋体抗体阴性。',
  },
];

// 通过 Vite 的 import.meta.glob 批量引入病例图片（构建时确定 URL）。
// 指定 import: 'default' 后，每个模块的值即为图片 URL 字符串。
const IMAGE_MODULES = import.meta.glob<string>('../assets/medical/*.png', {
  eager: true,
  query: '?url',
  import: 'default',
});

/** 图片元数据：文件名（不含扩展名）→ 展示信息。 */
const IMAGE_META: Record<
  string,
  { label: string; level: SensitivityLevel; desc: string }
> = {
  lab_blood_routine: { label: '血常规报告', level: 'L3', desc: '门诊血常规检验单，含身份证号与手机号' },
  outpatient_record: { label: '门诊病历', level: 'L3', desc: '普通门诊诊疗记录，含个人身份信息' },
  discharge_summary: { label: '出院小结', level: 'L4', desc: '完整住院病历（糖尿病伴酮症）' },
  psychiatric_record: { label: '精神科病历', level: 'L4', desc: '精神分裂症住院病历，敏感精神健康信息' },
  genetic_report: { label: '基因检测报告', level: 'L5', desc: 'BRCA1 致病突变，遗传信息' },
  hiv_lab_report: { label: '感染免疫报告', level: 'L5', desc: 'HIV 确证阳性，敏感传染病信息' },
};

/** 由 glob 结果与元数据合并得到图片病例列表（按元数据顺序）。 */
export const IMAGE_CASES: ImageCase[] = Object.entries(IMAGE_META)
  .map(([id, meta]) => {
    const url = IMAGE_MODULES[`../assets/medical/${id}.png`];
    return url ? { id, ...meta, url } : null;
  })
  .filter((c): c is ImageCase => c !== null);
