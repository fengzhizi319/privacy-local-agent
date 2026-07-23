/**
 * 错误边界组件：捕获子组件树渲染期抛出的异常，避免单组件崩溃导致整页白屏。
 *
 * 采用 React 类组件（错误边界目前仅支持 getDerivedStateFromError /
 * componentDidCatch 生命周期，函数组件无法实现）。捕获到错误后展示
 * 友好的降级界面，并提供“重试”按钮重置状态、重新渲染子树。
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Icon } from '@/components/icons';

interface ErrorBoundaryProps {
  /** 被保护的子组件树。 */
  children: ReactNode;
}

interface ErrorBoundaryState {
  /** 捕获到的错误；为 null 表示子树渲染正常。 */
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  /** 渲染期捕获子树异常，记录到 state 以切换到降级界面。 */
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  /** 错误上报钩子（此处仅记录到控制台，便于调试）。 */
  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('ErrorBoundary 捕获到渲染异常:', error, info.componentStack);
  }

  /** 重置错误状态，重新尝试渲染子树。 */
  private handleReset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 px-6">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-500">
            <Icon name="alert" className="h-6 w-6" />
          </span>
          <div className="text-center">
            <p className="text-sm font-medium text-gray-800">界面渲染出错</p>
            <p className="mt-1 max-w-md break-words text-xs text-gray-500">
              {this.state.error.message || '发生未知错误'}
            </p>
          </div>
          <button
            onClick={this.handleReset}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-700"
          >
            <Icon name="refresh" className="h-4 w-4" />
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
