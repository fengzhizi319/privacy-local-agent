/**
 * ErrorBoundary 单元测试：验证正常渲染与错误降级行为。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ErrorBoundary from '../ErrorBoundary';

// 模拟 Icon 组件（避免引入完整图标库）
vi.mock('@/components/icons', () => ({
  Icon: ({ name }: { name: string }) => <span data-testid={`icon-${name}`} />,
}));

/** 故意在渲染时抛错的组件。 */
function Bomb({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('测试爆炸');
  return <div data-testid="safe-content">正常内容</div>;
}

describe('ErrorBoundary', () => {
  it('子组件正常时渲染 children', () => {
    render(
      <ErrorBoundary>
        <Bomb shouldThrow={false} />
      </ErrorBoundary>,
    );

    expect(screen.getByTestId('safe-content')).toBeInTheDocument();
  });

  it('子组件抛错时展示降级界面', () => {
    // 抑制 React 默认的 console.error 输出
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Bomb shouldThrow={true} />
      </ErrorBoundary>,
    );

    expect(screen.getByText('界面渲染出错')).toBeInTheDocument();
    expect(screen.getByText('测试爆炸')).toBeInTheDocument();

    spy.mockRestore();
  });

  it('点击重试按钮后重新渲染子树', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});

    // 使用可控的 shouldThrow 状态
    let throwFlag = true;
    function ControlledBomb() {
      if (throwFlag) throw new Error('临时错误');
      return <div data-testid="recovered">已恢复</div>;
    }

    render(
      <ErrorBoundary>
        <ControlledBomb />
      </ErrorBoundary>,
    );

    // 初始应显示错误
    expect(screen.getByText('界面渲染出错')).toBeInTheDocument();

    // 修复错误后点击重试
    throwFlag = false;
    fireEvent.click(screen.getByText('重试'));

    expect(screen.getByTestId('recovered')).toBeInTheDocument();

    spy.mockRestore();
  });
});
