# Privacy Test Console - Frontend

React 18 + TypeScript + Vite + Tailwind CSS 实现的前端测试控制台。

以三栏布局（顶部导航 + 侧边导航 + 主区域）组织 `privacy-local-agent` 的全部可测试接口，支持示例加载、请求编辑、JSON 高亮响应、cURL 导出、请求历史与批量测试。

详细设计文档见 [`docs/console_web/`](../../docs/console_web/README.md)。

## 开发

```bash
cd console/web
npm install
# 启动开发服务器（自动代理 /api 到 127.0.0.1:8080）
npm run dev
```

开发服务器默认在 `http://127.0.0.1:5173` 启动，需先启动 Python 后端（`cd ../backend && ./run.sh`）。

## 构建

```bash
npm run build        # 等价于 tsc && vite build
```

产物输出到 `dist/`，由后端挂载为静态资源：

- Python 后端：`console/backend/app/main.py`（`PRIVACY_CONSOLE_STATIC_DIR`，默认 `../web/dist`）
- Go 后端：`console/backend-go`（同样挂载 `web/dist`）

## 主要文件

- `src/main.tsx` - 应用入口
- `src/App.tsx` - 根组件：全局状态 + 三栏布局 + 视图路由
- `src/api/client.ts` - 后端 API 调用封装（唯一 fetch 出口）
- `src/types/api.ts` - 前后端数据契约（TS 类型定义）
- `src/lib/categories.ts` - 分类元数据（顺序 / 图标 / 配色）
- `src/lib/curl.ts` - cURL 命令生成
- `src/lib/history.ts` - 请求历史（localStorage 持久化）
- `src/components/` - 视图与组件
  - `Header.tsx` - 顶栏（品牌 + 健康状态灯 + 后端切换）
  - `BackendSelector.tsx` - 后端切换下拉框
  - `Sidebar.tsx` - 侧边导航树（搜索 / 分组 / 折叠）
  - `Overview.tsx` - 总览页（分类卡片网格）
  - `EndpointView.tsx` - 端点测试页（请求 / 响应分栏）
  - `BatchTest.tsx` - 批量测试页
  - `ResponsePanel.tsx` - 响应查看器（JSON 高亮 / 复制 / 下载）
  - `HistoryPanel.tsx` - 请求历史面板
  - `icons.tsx` - 内联 SVG 图标库

## 技术栈

- React 18
- TypeScript 5
- Vite 5
- Tailwind CSS 3

## 代码质量

```bash
npx tsc --noEmit     # 类型检查
npm run lint         # ESLint
```
