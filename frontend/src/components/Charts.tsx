/** Small SVG charts in the Night style: thin marks, recessive grid, hover for exact values. */
import { type KeyboardEvent, type MouseEvent, type ReactNode, useId, useState } from "react";

import { useWidth } from "../hooks";

const PAD = { left: 56, right: 12, top: 10, bottom: 26 };

export function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) {
    const pad = Math.abs(min) * 0.1 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

function Legend({ items }: { items: { label: string; color: string; dashed?: boolean; bar?: boolean }[] }) {
  return (
    <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12, color: "var(--ink3)" }}>
      {items.map((s) => (
        <span key={s.label} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          {s.bar ? (
            <span style={{ width: 10, height: 10, borderRadius: 3, background: s.color }} />
          ) : (
            <span style={{ width: 16, height: 0, borderTop: `2px ${s.dashed ? "dashed" : "solid"} ${s.color}` }} />
          )}
          {s.label}
        </span>
      ))}
    </div>
  );
}

function Tooltip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(x + 12, 0), Math.max(0, width - 190));
  const flip = x + 200 > width;
  return (
    <div
      className="n"
      style={{
        position: "absolute",
        top: 0,
        left: flip ? Math.max(0, x - 200) : left,
        pointerEvents: "none",
        padding: "8px 12px",
        borderRadius: 10,
        background: "var(--hover)",
        border: "1px solid var(--line2)",
        fontSize: 12,
        display: "flex",
        flexDirection: "column",
        gap: 3,
        minWidth: 150,
        boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
      }}
    >
      {children}
    </div>
  );
}

export interface LineSeries {
  label: string;
  color: string;
  values: (number | null)[];
  dashed?: boolean;
  area?: boolean;
}

export function LineChart({
  labels,
  series,
  format,
  axisFormat = format,
  height = 220,
  ariaLabel,
  tipTitle = (i) => labels[i],
  showLegend = series.length > 1,
  everyNthLabel,
}: {
  labels: string[];
  series: LineSeries[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  height?: number;
  ariaLabel: string;
  tipTitle?: (i: number) => string;
  showLegend?: boolean;
  everyNthLabel?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const gid = useId().replace(/:/g, "");
  const n = labels.length;
  const values = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const ticks = values.length ? niceTicks(Math.min(...values), Math.max(...values)) : [0, 1];
  const lo = ticks[0];
  const hi = ticks[ticks.length - 1];
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n <= 1 ? plotW / 2 : (i * plotW) / (n - 1));
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const step = everyNthLabel ?? Math.max(1, Math.ceil(n / Math.max(2, Math.floor(plotW / 64))));

  function path(vals: (number | null)[]): string {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }

  function onMove(e: MouseEvent<SVGRectElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const f = (e.clientX - r.left) / r.width;
    setHover(Math.max(0, Math.min(n - 1, Math.round(f * (n - 1)))));
  }
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (e.key === "Escape") setHover(null);
  }

  const first = series[0];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {showLegend && <Legend items={series} />}
      <div
        ref={ref}
        style={{ position: "relative", width: "100%", outline: "none" }}
        tabIndex={0}
        role="img"
        aria-label={ariaLabel}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
      >
        {width > 0 && (
          <svg width={width} height={height} style={{ display: "block" }} aria-hidden="true">
            <defs>
              <linearGradient id={`g${gid}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" style={{ stopColor: first?.color, stopOpacity: 0.24 }} />
                <stop offset="1" style={{ stopColor: first?.color, stopOpacity: 0 }} />
              </linearGradient>
            </defs>
            {ticks.map((t, i) => (
              <g key={t}>
                <line
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={y(t)}
                  y2={y(t)}
                  style={{ stroke: i === 0 ? "var(--line2)" : "var(--line)" }}
                />
                <text x={0} y={y(t) + 4} style={{ fill: "var(--ink4)", fontSize: 11 }}>
                  {axisFormat(t)}
                </text>
              </g>
            ))}
            {labels.map((l, i) =>
              i % step === 0 || i === n - 1 ? (
                <text
                  key={i}
                  x={x(i)}
                  y={height - 6}
                  textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}
                  style={{ fill: "var(--ink4)", fontSize: 11 }}
                >
                  {l}
                </text>
              ) : null,
            )}
            {series.map(
              (s) =>
                s.area && (
                  <path
                    key={`a${s.label}`}
                    d={`${path(s.values)}L${x(n - 1)},${y(lo)}L${x(0)},${y(lo)}Z`}
                    fill={`url(#g${gid})`}
                  />
                ),
            )}
            {series.map((s) => (
              <path
                key={s.label}
                d={path(s.values)}
                style={{
                  fill: "none",
                  stroke: s.color,
                  strokeWidth: 2,
                  strokeDasharray: s.dashed ? "5 5" : undefined,
                  strokeLinejoin: "round",
                  strokeLinecap: "round",
                }}
              />
            ))}
            {hover !== null && (
              <g>
                <line
                  x1={x(hover)}
                  x2={x(hover)}
                  y1={PAD.top}
                  y2={PAD.top + plotH}
                  style={{ stroke: "var(--ink4)", strokeDasharray: "3 3" }}
                />
                {series.map((s) =>
                  s.values[hover] === null || s.values[hover] === undefined ? null : (
                    <circle
                      key={s.label}
                      cx={x(hover)}
                      cy={y(s.values[hover] as number)}
                      r={5}
                      style={{ fill: s.color, stroke: "var(--card)", strokeWidth: 2 }}
                    />
                  ),
                )}
              </g>
            )}
            <rect
              x={PAD.left}
              y={0}
              width={plotW}
              height={height - PAD.bottom}
              fill="transparent"
              onMouseMove={onMove}
              onMouseLeave={() => setHover(null)}
              style={{ cursor: "crosshair" }}
            />
          </svg>
        )}
        {hover !== null && width > 0 && (
          <Tooltip x={x(hover)} width={width}>
            <span style={{ color: "var(--ink3)" }}>{tipTitle(hover)}</span>
            {series.map((s) =>
              s.values[hover] === null || s.values[hover] === undefined ? null : (
                <span key={s.label} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  {series.length > 1 && <span style={{ color: "var(--ink3)" }}>{s.label}</span>}
                  <strong style={{ fontSize: 14 }}>{format(s.values[hover] as number)}</strong>
                </span>
              ),
            )}
          </Tooltip>
        )}
      </div>
      <div className="sr-only">
        <table>
          <caption>{ariaLabel}</caption>
          <tbody>
            {labels.map((_, i) => (
              <tr key={i}>
                <th scope="row">{tipTitle(i)}</th>
                {series.map((s) => (
                  <td key={s.label}>{s.values[i] === null ? "" : format(s.values[i] as number)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export interface BarSeries {
  label: string;
  color: string;
  values: number[];
}

function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  if (h <= 0) return "";
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function BarChart({
  labels,
  series,
  format,
  axisFormat = format,
  height = 220,
  ariaLabel,
  tipTitle = (i) => labels[i],
}: {
  labels: string[];
  series: BarSeries[];
  format: (v: number) => string;
  axisFormat?: (v: number) => string;
  height?: number;
  ariaLabel: string;
  tipTitle?: (i: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const ticks = niceTicks(0, Math.max(1, ...series.flatMap((s) => s.values)));
  const hi = ticks[ticks.length - 1];
  const plotW = Math.max(1, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const y = (v: number) => PAD.top + plotH - (v / hi) * plotH;
  const groupW = plotW / Math.max(1, n);
  const barW = Math.max(3, Math.min(18, (groupW * 0.72 - 2 * (series.length - 1)) / series.length));
  const groupInner = barW * series.length + 2 * (series.length - 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Legend items={series.map((s) => ({ ...s, bar: true }))} />
      <div
        ref={ref}
        style={{ position: "relative", width: "100%", outline: "none" }}
        tabIndex={0}
        role="img"
        aria-label={ariaLabel}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
          else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
          else if (e.key === "Escape") setHover(null);
        }}
        onBlur={() => setHover(null)}
      >
        {width > 0 && (
          <svg width={width} height={height} style={{ display: "block" }} aria-hidden="true">
            {ticks.map((t, i) => (
              <g key={t}>
                <line
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={y(t)}
                  y2={y(t)}
                  style={{ stroke: i === 0 ? "var(--line2)" : "var(--line)" }}
                />
                <text x={0} y={y(t) + 4} style={{ fill: "var(--ink4)", fontSize: 11 }}>
                  {axisFormat(t)}
                </text>
              </g>
            ))}
            {labels.map((l, i) => {
              const gx = PAD.left + i * groupW;
              const start = gx + (groupW - groupInner) / 2;
              return (
                <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  <rect x={gx} y={0} width={groupW} height={height} fill="transparent" />
                  {hover === i && (
                    <rect x={gx + 2} y={PAD.top} width={groupW - 4} height={plotH} rx={6} style={{ fill: "var(--raised)" }} />
                  )}
                  {series.map((s, k) => (
                    <path
                      key={s.label}
                      d={topRounded(start + k * (barW + 2), y(s.values[i]), barW, y(0) - y(s.values[i]), 4)}
                      style={{ fill: s.color }}
                    />
                  ))}
                  <text x={gx + groupW / 2} y={height - 6} textAnchor="middle" style={{ fill: "var(--ink4)", fontSize: 11 }}>
                    {l}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {hover !== null && width > 0 && (
          <Tooltip x={PAD.left + hover * groupW + groupW / 2} width={width}>
            <span style={{ color: "var(--ink3)" }}>{tipTitle(hover)}</span>
            {series.map((s) => (
              <span key={s.label} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ink3)" }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color }} />
                  {s.label}
                </span>
                <strong>{format(s.values[hover])}</strong>
              </span>
            ))}
          </Tooltip>
        )}
      </div>
      <div className="sr-only">
        <table>
          <caption>{ariaLabel}</caption>
          <thead>
            <tr>
              <th />
              {series.map((s) => (
                <th key={s.label}>{s.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {labels.map((_, i) => (
              <tr key={i}>
                <th scope="row">{tipTitle(i)}</th>
                {series.map((s) => (
                  <td key={s.label}>{format(s.values[i])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
