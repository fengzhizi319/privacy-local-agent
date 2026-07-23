/// <reference types="vite/client" />

/**
 * 构建期环境变量类型声明。
 *
 * ``VITE_CONSOLE_API_KEY``：可选控制台 API Key（对应后端 ``CONSOLE_API_KEY``），
 * 设置后前端请求会携带 ``Authorization: Bearer`` 头；未设置则不影响本地开发。
 */
interface ImportMetaEnv {
  readonly VITE_CONSOLE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

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
