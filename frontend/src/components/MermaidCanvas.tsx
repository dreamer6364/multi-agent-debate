import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

/**
 * Mermaid 画布（v2.5）：把后端现场生成的 mermaid 源码渲染成图，并支持缩放 / 平移 / 平滑刷新。
 *
 * 设计要点：
 * - mermaid 体积大，走**动态 import**，首屏不为图付代价；
 * - 渲染失败（语法错、库加载失败）不能让整页白屏——回落到可折叠的源码视图；
 * - 缩放用 `transform: scale`，平移用容器滚动（拖拽改 scrollLeft/Top）：
 *   这样滚动条与拖拽不会互相打架，长图也不会被裁掉；
 * - 每次换图给容器换 key，让 `mg-fade` 动画重放，产生"平滑刷新"而不是硬切。
 */

type MermaidAPI = {
  initialize: (config: Record<string, unknown>) => void;
  render: (id: string, code: string) => Promise<{ svg: string }>;
};

let loader: Promise<MermaidAPI> | null = null;

/** 单例加载 + 单例初始化：多处画布共用一份 mermaid，避免重复 init 造成配置漂移。 */
function loadMermaid(): Promise<MermaidAPI> {
  if (loader) return loader;
  loader = (async () => {
    const mod = await import("mermaid");
    const api = (mod.default ?? mod) as unknown as MermaidAPI;
    api.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      fontFamily: "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', sans-serif",
      themeVariables: {
        primaryColor: "#eef0fa",
        primaryBorderColor: "#6d5dfc",
        primaryTextColor: "#333333",
        lineColor: "#8a93a5",
        fontSize: "13px",
      },
      // 行距收紧：控制图是一条长链，默认 50px 行距一屏只放得下两三步
      flowchart: { curve: "basis", htmlLabels: true, padding: 10, nodeSpacing: 20, rankSpacing: 34, useMaxWidth: false },
    });
    return api;
  })();
  return loader;
}

const MIN_SCALE = 0.25;
const MAX_SCALE = 3;
/**
 * 自动适配的可读下限：整图塞进视口会把 13px 字压到 6px（用户实测"糊成一片"）。
 * 低于 80% 就宁可保留滚动条，让用户能读字。
 */
const MIN_FIT = 0.8;

export interface MermaidCanvasProps {
  /** mermaid 源码（由后端按辩论状态生成） */
  code: string;
  /** 图版本号：变化即重放刷新动画 */
  version?: number | string;
  /** 画布高度（px），默认 460 */
  height?: number;
  emptyHint?: string;
  testId?: string;
}

export function MermaidCanvas({
  code, version, height = 460, emptyHint = "暂无图数据", testId = "mermaid-canvas",
}: MermaidCanvasProps) {
  const [svg, setSvg] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1);
  const [showSource, setShowSource] = useState(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  /** 用户一旦手动缩放/平移，后续新图不再自动适配（否则每来一版图都把用户的视图拽回去） */
  const touched = useRef(false);
  const reactId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const seq = useRef(0);

  /** 量出图与容器的比例（mermaid 输出带 viewBox，直接按它算，不依赖布局时序） */
  const ratio = useCallback((mode: "width" | "all") => {
    const stage = stageRef.current;
    const node = stage?.querySelector("svg") as SVGSVGElement | null;
    const box = node?.viewBox?.baseVal;
    if (!stage || !box || !box.width || !box.height) return null;
    const byW = (stage.clientWidth - 16) / box.width;
    const byH = (stage.clientHeight - 16) / box.height;
    return mode === "width" ? byW : Math.min(byW, byH);
  }, []);

  /**
   * 默认"按宽度适配"而不是"塞进整屏"：控制图是一条很长的纵向链，
   * 塞进 420px 高会被压到 25%（字全糊了）；按宽度铺满、上下滚动反而能读。
   * 70 多个节点的论证图横向很宽，也允许横向滚动而不是无限缩小。
   */
  const fit = useCallback((mode: "width" | "all" = "width") => {
    const k = ratio(mode);
    const stage = stageRef.current;
    if (k === null || !stage) return;
    setScale(Math.max(MIN_FIT, Math.min(1, Number(k.toFixed(3)))));
    stage.scrollLeft = 0; // jsdom 没有 scrollTo，直接写滚动偏移（浏览器同样生效）
    stage.scrollTop = 0;
  }, [ratio]);

  /** 把当前活跃节点滚到视野中央：图会一直变长，不跟着走就得手动追 */
  const revealActive = useCallback(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const active = stage.querySelector("[class*='active']");
    if (!active) return;
    const box = active.getBoundingClientRect();
    const view = stage.getBoundingClientRect();
    if (!box.height && !box.width) return; // jsdom 全 0：跳过
    stage.scrollLeft += box.left - view.left - (stage.clientWidth - box.width) / 2;
    stage.scrollTop += box.top - view.top - (stage.clientHeight - box.height) / 2;
  }, []);

  /** 「适应宽度」＝回到自动模式：重新按宽度适配，并恢复"跟随当前节点" */
  const adapt = useCallback(() => {
    touched.current = false;
    fit();
    requestAnimationFrame(revealActive);
  }, [fit, revealActive]);

  useEffect(() => {
    if (!code.trim()) {
      setSvg("");
      setError(null);
      return;
    }
    let alive = true;
    seq.current += 1;
    const renderId = `mmd-${reactId}-${seq.current}`;
    void (async () => {
      try {
        const mermaid = await loadMermaid();
        const { svg: out } = await mermaid.render(renderId, code);
        if (alive) {
          setSvg(out);
          setError(null);
        }
      } catch (err) {
        if (alive) {
          setSvg("");
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [code, reactId]);

  // 新图渲染完 → 自动按宽度适配；用户手动调过视图后就不再干预
  useEffect(() => {
    if (svg && !touched.current) fit();
  }, [svg, version, fit]);

  // 缩放/换版落地后再对齐活跃节点（要等 transform 生效，rect 才是新坐标）
  useEffect(() => {
    if (!svg || touched.current) return;
    const id = requestAnimationFrame(revealActive);
    return () => cancelAnimationFrame(id);
  }, [svg, scale, version, revealActive]);

  const zoomBy = useCallback((factor: number) => {
    touched.current = true;
    setScale((s) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number((s * factor).toFixed(2)))));
  }, []);

  const reset = useCallback(() => {
    touched.current = true;
    setScale(1);
    const stage = stageRef.current;
    if (stage) {
      stage.scrollLeft = 0;
      stage.scrollTop = 0;
    }
  }, []);

  const onWheel = useCallback((event: WheelEvent) => {
    // Ctrl/⌘ + 滚轮缩放；普通滚轮留给容器滚动（触控板两指滑动也能正常看图）
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    touched.current = true;
    setScale((s) => {
      const next = event.deltaY > 0 ? s * 0.92 : s * 1.08;
      return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(next.toFixed(3))));
    });
  }, []);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    const stage = stageRef.current;
    if (!stage) return;
    drag.current = {
      x: event.clientX, y: event.clientY,
      left: stage.scrollLeft, top: stage.scrollTop,
    };
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent) => {
    const start = drag.current;
    const stage = stageRef.current;
    if (!start || !stage) return;
    touched.current = true;
    stage.scrollLeft = start.left - (event.clientX - start.x);
    stage.scrollTop = start.top - (event.clientY - start.y);
  }, []);

  const onPointerUp = useCallback(() => {
    drag.current = null;
  }, []);

  const meta = useMemo(
    () => ({ pct: Math.round(scale * 100), canSource: Boolean(code) }),
    [scale, code],
  );

  if (!code.trim()) {
    return (
      <div className="mg-empty" style={{ height }} data-testid={`${testId}-empty`}>
        {emptyHint}
      </div>
    );
  }

  return (
    <div className="mg" data-testid={testId}>
      <div className="mg-toolbar">
        <span className="mg-meta">缩放 {meta.pct}%</span>
        <button type="button" onClick={() => zoomBy(0.85)} title="缩小">－</button>
        <button type="button" onClick={() => zoomBy(1.18)} title="放大">＋</button>
        <button type="button" onClick={adapt} title="适应宽度">适应</button>
        <button type="button" onClick={reset} title="复位">复位</button>
        {meta.canSource && (
          <button type="button" onClick={() => setShowSource((v) => !v)} title="查看 mermaid 源码">
            {showSource ? "收起源码" : "源码"}
          </button>
        )}
        {error && <span className="mg-err">渲染失败，已回落到源码</span>}
      </div>
      <div
        ref={stageRef}
        className="mg-stage"
        style={{ height }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
      >
        {svg ? (
          <div
            key={`${version}-${seq.current}`}
            className="mg-inner mg-in"
            style={{ transform: `scale(${scale})`, transformOrigin: "0 0" }}
            // mermaid 渲染结果由本系统后端生成、securityLevel=strict，无用户可控的脚本注入面
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        ) : (
          <pre className="mg-source">{code}</pre>
        )}
      </div>
      {showSource && <pre className="mg-source" data-testid={`${testId}-source`}>{code}</pre>}
    </div>
  );
}
