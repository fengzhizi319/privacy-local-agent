/**
 * FileTest validateFile 单元测试：验证客户端文件预校验逻辑。
 */
import { describe, it, expect } from 'vitest';
import { validateFile, MAX_UPLOAD_BYTES, ACCEPTED_EXTS } from '../FileTest';

/** 构造指定名称和大小的 File 对象。 */
function makeFile(name: string, sizeBytes: number): File {
  // 用稀疏内容模拟大小（jsdom 不关心实际内容）
  const content = sizeBytes > 0 ? 'x'.repeat(Math.min(sizeBytes, 1024)) : '';
  const file = new File([content], name, { type: 'text/csv' });
  // File.size 由 content 长度决定，需 mock 大文件场景
  if (sizeBytes > 1024) {
    Object.defineProperty(file, 'size', { value: sizeBytes });
  }
  return file;
}

describe('validateFile', () => {
  it('合法 .csv 文件通过', () => {
    const f = makeFile('data.csv', 1024);
    expect(validateFile(f)).toBeNull();
  });

  it('合法 .json 文件通过', () => {
    const f = makeFile('records.json', 2048);
    expect(validateFile(f)).toBeNull();
  });

  it('大写扩展名同样通过（大小写不敏感）', () => {
    const f = makeFile('DATA.CSV', 512);
    expect(validateFile(f)).toBeNull();
  });

  it('不支持的扩展名返回错误', () => {
    const f = makeFile('image.png', 1024);
    expect(validateFile(f)).toBe('仅支持 .csv 与 .json 文件');
  });

  it('无扩展名文件返回错误', () => {
    const f = makeFile('noext', 100);
    expect(validateFile(f)).toBe('仅支持 .csv 与 .json 文件');
  });

  it('超过大小上限返回错误', () => {
    const f = makeFile('big.csv', MAX_UPLOAD_BYTES + 1);
    const result = validateFile(f);
    expect(result).toContain('文件过大');
    expect(result).toContain('上限 10 MB');
  });

  it('恰好等于上限时通过', () => {
    const f = makeFile('exact.csv', MAX_UPLOAD_BYTES);
    expect(validateFile(f)).toBeNull();
  });

  it('ACCEPTED_EXTS 包含 csv 和 json', () => {
    expect(ACCEPTED_EXTS).toContain('.csv');
    expect(ACCEPTED_EXTS).toContain('.json');
  });
});
