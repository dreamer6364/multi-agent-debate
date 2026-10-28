import { Fragment, type ReactNode } from "react";

/**
 * 轻量富文本渲染（CONSTRAINTS F4：禁用 dangerouslySetInnerHTML，纯 JSX 组装）：
 * - 成对 `**加粗**` → <strong>（模型常把"定义与判准""论点一"当小标题加粗）
 * - 换行保留为段落（pre-wrap 之外的显式 <br/>，流式输出时也不必等整段）
 * - 落单的星号直接剔除：历史脏数据里有裸 `*`，不应再出现在屏幕上
 */
const BOLD = /(\*\*[^*\n]+\*\*)/g;

function renderInline(line: string, keyPrefix: string): ReactNode[] {
  const parts = line.split(BOLD).filter((p) => p !== "");
  return parts.map((part, i) => {
    if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${keyPrefix}-b${i}`}>{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={`${keyPrefix}-t${i}`}>{part.replace(/\*/g, "")}</Fragment>;
  });
}

export function RichText({ text, className }: { text?: string | null; className?: string }) {
  const body = text ?? "";
  const lines = body.split("\n");
  return (
    <span className={className}>
      {lines.map((line, i) => (
        <Fragment key={`ln-${i}`}>
          {i > 0 && <br />}
          {renderInline(line, `ln-${i}`)}
        </Fragment>
      ))}
    </span>
  );
}
