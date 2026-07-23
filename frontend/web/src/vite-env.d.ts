/// <reference types="vite/client" />

/**
 * Vite 客户端类型补充。
 *
 * 显式声明图片资源的模块类型与 ``import.meta.glob`` 的 ``?url`` 用法，
 * 便于 ``medicalCases.ts`` 以类型安全方式批量引入病例测试图片。
 */
declare module '*.png' {
  const src: string;
  export default src;
}

declare module '*.jpg' {
  const src: string;
  export default src;
}

declare module '*.jpeg' {
  const src: string;
  export default src;
}
