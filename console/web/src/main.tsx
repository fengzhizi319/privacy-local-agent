/**
 * 应用入口：挂载 React 根组件。
 *
 * 使用 React 18 的 createRoot API；StrictMode 会在开发环境下
 * 双重调用渲染以帮助发现副作用问题（不影响生产构建）。
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
