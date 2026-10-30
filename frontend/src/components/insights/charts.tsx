import { useId, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { gbpCompact, niceScale } from "../../lib/insights";

/**
 * The small chart set Insights needs, drawn as plain SVG: no chart library is installed and
 * these are all simple shapes. Everything is direct-labelled or titled, and series differ by
 * line style or position as well as colour, so none of it relies on colour alone.
 */

export const CHART_COLORS = {
  accent: "#2563eb",
  warm: "#c2410c",
  muted: "#64748b",
  grid: "#e2e8f0",
  text: "#475569",
  strong: "#0f172a",
  soft: "#f1f5f9",
} as const;

const W = 780;

// --- Sparkline --------------------------------------------------------------------------

export function Sparkline({ values, color = CHART_COLORS.accent }: { values: number[]; color?: string }) {
  if (values.length < 2) return <span className="text-slate-300">-</span>;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => `${((i / (values.length - 1)) * 68 + 1).toFixed(1)},${(20 - ((v - min) / span) * 18).toFixed(1)}`)
    .join(" ");
  return (
    <svg width="70" height="22" viewBox="0 0 70 22" aria-hidden="true" className="inline-block align-middle">
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  );
}

// --- Line chart -------------------------------------------------------------------------

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  /** One value per slot; null leaves a gap. May be shorter than `slots` (a year not over yet). */
  values: (number | null)[];
  dashed?: boolean;
  /** Draw the series name beside its last point. */
  direct?: boolean;
}

export interface LineTooltip {
  title: string;
  lines: { label: string; value: string; color: string }[];
}

const LEFT = 56;
const RIGHT = 690;
const TOP = 14;
const BOTTOM = 214;

export function LineChart({
  slots,
  series,
  dataCount,
  axisFormat,
  xLabels,
  tooltip,
  ariaLabel,
  futureLabel = "Not yet reached",
}: {
  slots: string[];
  series: LineSeries[];
  /** How many leading slots have real data; the rest are shaded as still to come. */
  dataCount: number;
  axisFormat: (value: number) => string;
  xLabels: { index: number; label: string }[];
  tooltip: (index: number) => LineTooltip;
  ariaLabel: string;
  futureLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const n = slots.length;

  const all = series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const scale = niceScale(all.length ? Math.min(...all) : 0, all.length ? Math.max(...all) : 1);
  const x = (i: number) => LEFT + (n > 1 ? i / (n - 1) : 0.5) * (RIGHT - LEFT);
  const y = (v: number) => BOTTOM - ((v - scale.low) / (scale.high - scale.low || 1)) * (BOTTOM - TOP);

  const path = (values: (number | null)[]) => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  const lastIndex = (values: (number | null)[]) => {
    for (let i = values.length - 1; i >= 0; i--) if (values[i] !== null) return i;
    return -1;
  };

  function indexAt(event: MouseEvent<SVGSVGElement>): number {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || n === 0) return 0;
    const px = ((event.clientX - rect.left) / rect.width) * W;
    const raw = ((px - LEFT) / (RIGHT - LEFT)) * (n - 1);
    return Math.max(0, Math.min(n - 1, Math.round(raw)));
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (event.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (event.key === "Escape") setHover(null);
    else return;
    event.preventDefault();
  }

  const tip = hover !== null ? tooltip(hover) : null;
  const futureStart = dataCount < n ? x(Math.max(0, dataCount - 1)) : null;

  return (
    <div className="relative">
      <svg
        ref={ref}
        viewBox={`0 0 ${W} 240`}
        width="100%"
        role="img"
        aria-label={ariaLabel}
        tabIndex={0}
        onMouseMove={(e) => setHover(indexAt(e))}
        onMouseLeave={() => setHover(null)}
        onKeyDown={onKeyDown}
        onBlur={() => setHover(null)}
        className="block outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
      >
        {futureStart !== null && (
          <>
            <rect x={futureStart} y={TOP} width={RIGHT - futureStart} height={BOTTOM - TOP} fill={CHART_COLORS.soft} />
            <text x={(futureStart + RIGHT) / 2} y={TOP + 14} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted}>
              {futureLabel}
            </text>
          </>
        )}
        {scale.values.map((v) => (
          <g key={v}>
            <line x1={LEFT} x2={RIGHT} y1={y(v)} y2={y(v)} stroke={CHART_COLORS.grid} />
            <text x={LEFT - 6} y={y(v) + 3.5} textAnchor="end" fontSize="10.5" fill={CHART_COLORS.muted}>
              {axisFormat(v)}
            </text>
          </g>
        ))}
        {xLabels.map((l) => (
          <text key={l.index} x={x(l.index)} y={BOTTOM + 18} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted}>
            {l.label}
          </text>
        ))}
        {series.map((s) => (
          <path
            key={s.key}
            d={path(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={s.dashed ? 1.5 : 2.5}
            strokeDasharray={s.dashed ? "4 4" : undefined}
            strokeLinejoin="round"
          />
        ))}
        {series
          .filter((s) => s.direct)
          .map((s) => {
            const i = lastIndex(s.values);
            if (i < 0) return null;
            return (
              <text key={s.key} x={x(i) + 8} y={y(s.values[i] as number) + 3.5} fontSize="11" fontWeight="600" fill={s.color}>
                {s.label}
              </text>
            );
          })}
        {hover !== null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={TOP} y2={BOTTOM} stroke={CHART_COLORS.muted} strokeDasharray="2 3" />
            {series.map((s) => {
              const v = s.values[hover];
              return v === null || v === undefined ? null : (
                <circle key={s.key} cx={x(hover)} cy={y(v)} r="4" fill="#fff" stroke={s.color} strokeWidth="2" />
              );
            })}
          </>
        )}
      </svg>
      {tip && hover !== null && (
        <div
          role="status"
          className="pointer-events-none absolute top-1 z-10 min-w-[150px] rounded-md bg-slate-900 px-2.5 py-2 text-[11.5px] text-white shadow-lg"
          style={{
            left: `${(x(hover) / W) * 100}%`,
            transform: hover > n / 2 ? "translateX(calc(-100% - 10px))" : "translateX(10px)",
          }}
        >
          <p className="mb-1 font-semibold">{tip.title}</p>
          {tip.lines.map((line) => (
            <p key={line.label} className="flex justify-between gap-3 tabular-nums">
              <span className="flex items-center gap-1.5">
                <span className="inline-block h-2 w-2 rounded-full" style={{ background: line.color }} aria-hidden="true" />
                {line.label}
              </span>
              <span>{line.value}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Waterfall --------------------------------------------------------------------------

export interface WaterfallStep {
  key: string;
  label: string;
  /** Positive for the start and end totals; negative for each cost taken off. */
  amount: number;
  kind: "start" | "cost" | "end";
}

export function Waterfall({ steps, ariaLabel }: { steps: WaterfallStep[]; ariaLabel: string }) {
  const top = steps.find((s) => s.kind === "start")?.amount || Math.max(...steps.map((s) => Math.abs(s.amount)), 1);
  const base = 218;
  const height = 176;
  const scale = height / (top || 1);
  const slot = (W - 20) / Math.max(steps.length, 1);
  const barWidth = Math.min(64, slot - 14);
  let running = 0;

  return (
    <svg viewBox={`0 0 ${W} 262`} width="100%" role="img" aria-label={ariaLabel} className="block">
      <line x1="10" x2={W - 10} y1={base} y2={base} stroke={CHART_COLORS.grid} />
      {steps.map((step, i) => {
        let from: number;
        let to: number;
        if (step.kind === "cost") {
          from = running;
          to = running + step.amount;
          running = to;
        } else {
          from = 0;
          to = step.amount;
          running = step.amount;
        }
        const hi = Math.max(from, to);
        const lo = Math.min(from, to);
        const barTop = base - hi * scale;
        const barHeight = Math.max(2, (hi - lo) * scale);
        const cx = 10 + slot * i + slot / 2;
        const fill = step.kind === "cost" ? CHART_COLORS.muted : step.kind === "start" ? CHART_COLORS.accent : CHART_COLORS.warm;
        const sign = step.kind === "cost" ? "−" : "";
        return (
          <g key={step.key}>
            <rect x={cx - barWidth / 2} y={barTop} width={barWidth} height={barHeight} fill={fill} rx="2">
              <title>{`${step.label}: ${sign}${gbpCompact(Math.abs(step.amount))}`}</title>
            </rect>
            <text x={cx} y={barTop - 5} textAnchor="middle" fontSize="11" fontWeight="600" fill={CHART_COLORS.strong}>
              {sign}
              {formatWhole(Math.abs(step.amount))}
            </text>
            <text x={cx} y={base + 16} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted}>
              {step.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function formatWhole(n: number): string {
  return `£${Math.round(n).toLocaleString("en-GB")}`;
}

// --- Scatter ----------------------------------------------------------------------------

export interface ScatterPoint {
  key: string;
  label: string;
  x: number;
  y: number;
  /** Bubble area follows this (net profit); must be positive. */
  size: number;
  title: string;
}

export function Scatter({
  points,
  xLabel,
  yLabel,
  yFormat,
  ariaLabel,
}: {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  yFormat: (v: number) => string;
  ariaLabel: string;
}) {
  const id = useId();
  if (points.length === 0) return null;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const xScale = niceScale(0, Math.max(...xs));
  const yScale = niceScale(Math.min(...ys, 0), Math.max(...ys));
  const L = 60;
  const R = 760;
  const T = 10;
  const B = 262;
  const px = (v: number) => L + ((v - xScale.low) / (xScale.high - xScale.low || 1)) * (R - L);
  const py = (v: number) => B - ((v - yScale.low) / (yScale.high - yScale.low || 1)) * (B - T);
  const maxSize = Math.max(...points.map((p) => p.size), 1);
  const radius = (size: number) => 6 + 18 * Math.sqrt(Math.max(size, 0) / maxSize);
  const median = (values: number[]) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const midX = px(median(xs));
  const midY = py(median(ys));
  const labelled = new Set([...points].sort((a, b) => b.size - a.size).slice(0, 6).map((p) => p.key));

  return (
    <svg viewBox={`0 0 ${W} 300`} width="100%" role="img" aria-label={ariaLabel} className="block">
      {yScale.values.map((v) => (
        <g key={`y${v}`}>
          <line x1={L} x2={R} y1={py(v)} y2={py(v)} stroke={CHART_COLORS.grid} />
          <text x={L - 6} y={py(v) + 3.5} textAnchor="end" fontSize="10.5" fill={CHART_COLORS.muted}>
            {yFormat(v)}
          </text>
        </g>
      ))}
      {xScale.values.map((v) => (
        <text key={`x${v}`} x={px(v)} y={B + 16} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted}>
          {Math.round(v)}
        </text>
      ))}
      <line x1={midX} x2={midX} y1={T} y2={B} stroke={CHART_COLORS.muted} strokeDasharray="3 3" />
      <line x1={L} x2={R} y1={midY} y2={midY} stroke={CHART_COLORS.muted} strokeDasharray="3 3" />
      {[
        { x: L + 6, y: T + 12, anchor: "start", text: "Low volume, high margin" },
        { x: R - 6, y: T + 12, anchor: "end", text: "Stars" },
        { x: L + 6, y: B - 6, anchor: "start", text: "Reconsider" },
        { x: R - 6, y: B - 6, anchor: "end", text: "High volume, low margin" },
      ].map((q) => (
        <text key={q.text} x={q.x} y={q.y} textAnchor={q.anchor as "start" | "end"} fontSize="10" letterSpacing=".05em" fill={CHART_COLORS.muted}>
          {q.text.toUpperCase()}
        </text>
      ))}
      {points.map((p) => (
        <g key={p.key}>
          <circle cx={px(p.x)} cy={py(p.y)} r={radius(p.size)} fill="#dbeafe" fillOpacity="0.85" stroke={CHART_COLORS.accent} strokeWidth="1.5">
            <title>{p.title}</title>
          </circle>
          {labelled.has(p.key) && (
            <text x={px(p.x)} y={py(p.y) + 3.5} textAnchor="middle" fontSize="10" fontWeight="600" fill={CHART_COLORS.strong} id={`${id}-${p.key}`}>
              {p.label.length > 14 ? `${p.label.slice(0, 12)}…` : p.label}
            </text>
          )}
        </g>
      ))}
      <text x={(L + R) / 2} y={292} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted}>
        {xLabel}
      </text>
      <text x="14" y={(T + B) / 2} textAnchor="middle" fontSize="10.5" fill={CHART_COLORS.muted} transform={`rotate(-90 14 ${(T + B) / 2})`}>
        {yLabel}
      </text>
    </svg>
  );
}

// --- Bars -------------------------------------------------------------------------------

/** Labelled horizontal bars, longest first. Widths are relative to the largest. */
export function BarList({ rows, color }: { rows: { key: string; label: string; value: number; text: string }[]; color: string }) {
  const max = Math.max(...rows.map((r) => r.value), 1);
  if (rows.length === 0) return <p className="text-xs text-slate-400">Nothing recorded in this period.</p>;
  return (
    <ul className="flex flex-col gap-2">
      {rows.map((row) => (
        <li key={row.key} className="grid grid-cols-[130px_1fr_72px] items-center gap-2 text-[12.5px]">
          <span className="truncate" title={row.label}>
            {row.label}
          </span>
          <span className="h-2 rounded-sm bg-slate-100" aria-hidden="true">
            <span className="block h-2 rounded-sm" style={{ width: `${(row.value / max) * 100}%`, background: color }} />
          </span>
          <span className="text-right tabular-nums">{row.text}</span>
        </li>
      ))}
    </ul>
  );
}

export interface StackSegment {
  key: string;
  label: string;
  value: number;
  color: string;
}

/** One horizontal bar split into labelled parts; the label sits inside when it fits. */
export function StackedBar({ segments, ariaLabel }: { segments: StackSegment[]; ariaLabel: string }): ReactNode {
  const total = segments.reduce((sum, s) => sum + Math.max(s.value, 0), 0);
  if (total <= 0) return null;
  return (
    <div role="img" aria-label={ariaLabel} className="flex h-[22px] overflow-hidden rounded-sm text-[10.5px] text-white">
      {segments.map((s) => {
        const share = (Math.max(s.value, 0) / total) * 100;
        return (
          <div
            key={s.key}
            title={`${s.label}: ${share.toFixed(0)}%`}
            style={{ width: `${share}%`, background: s.color }}
            className="overflow-hidden whitespace-nowrap px-1.5 leading-[22px]"
          >
            {share >= 9 ? `${s.label} ${share.toFixed(0)}` : ""}
          </div>
        );
      })}
    </div>
  );
}
