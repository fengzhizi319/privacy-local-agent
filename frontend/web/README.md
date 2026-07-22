# Privacy Test Console - Frontend

React + TypeScript + Vite 实现的前端测试控制台。

## 开发

```bash
cd frontend/web
# 安装依赖
corepack pnpm install
# 启动开发服务器（自动代理 /api 到 127.0.0.1:8080）
corepack pnpm dev
```

开发服务器默认在 `http://127.0.0.1:5173` 启动。

## 构建

```bash
corepack pnpm build
```

产物输出到 `dist/`，由后端 `frontend/backend/app/main.py` 挂载为静态资源。

## 主要文件

- `src/App.tsx` - 主布局与状态管理
- `src/components/Sidebar.tsx` - 左侧功能分组导航
- `src/components/RequestForm.tsx` - 请求体编辑与发送
- `src/components/ResponseViewer.tsx` - 响应/错误展示
- `src/api/client.ts` - 后端 API 调用封装
- `src/types/api.ts` - TypeScript 类型定义

## 技术栈

- React 18
- TypeScript 5
- Vite 5
- Tailwind CSS 3
