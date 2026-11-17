import { type CSSProperties, type ReactNode, useEffect, useId, useRef } from "react";

import { money } from "../format";

export function PageHead({
  cap,
  title,
  sub,
  actions,
}: {
  cap?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        {cap && <span className="cap">{cap}</span>}
        <h1 className="d" style={{ margin: 0, fontSize: 36, fontWeight: 800, letterSpacing: -0.8, lineHeight: 1.05 }}>
          {title}
        </h1>
        {sub && <span className="sub" style={{ fontSize: 14 }}>{sub}</span>}
      </div>
      {actions && <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>{actions}</div>}
    </div>
  );
}

type Tone = "up" | "down" | "warn" | "acc" | "plain";
const TONES: Record<Tone, CSSProperties> = {
  up: { background: "var(--up-tint)", color: "var(--up)" },
  down: { background: "var(--down-tint)", color: "var(--down-ink)" },
  warn: { background: "var(--warn-tint)", color: "var(--warn-ink)" },
  acc: { background: "var(--acc-tint)", color: "var(--acc-ink)" },
  plain: { background: "var(--raised)", color: "var(--ink3)" },
};

export function Chip({ tone = "plain", children, style }: { tone?: Tone; children: ReactNode; style?: CSSProperties }) {
  return (
    <span className="chip" style={{ ...TONES[tone], ...style }}>
      {children}
    </span>
  );
}

/** A change in money: ▲/▼ plus sign, teal or red. Never colour alone. */
export function Delta({ paise, suffix }: { paise: number; suffix?: string }) {
  if (paise === 0) return <Chip>No change{suffix ? ` ${suffix}` : ""}</Chip>;
  const up = paise > 0;
  return (
    <Chip tone={up ? "up" : "down"} style={{ fontSize: 13, padding: "6px 12px", alignSelf: "flex-start" }}>
      {up ? "▲" : "▼"} {money(Math.abs(paise), { exact: false })}
      {suffix ? ` ${suffix}` : ""}
    </Chip>
  );
}

/** An amount in a list: money in shows in teal with a +, money out stays plain. */
export function Amount({ paise, strong = true }: { paise: number; strong?: boolean }) {
  return (
    <span className="n" style={{ fontWeight: strong ? 600 : 400, color: paise > 0 ? "var(--up)" : "var(--ink)", whiteSpace: "nowrap" }}>
      {money(paise, { sign: true })}
    </span>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="card" style={{ borderColor: "var(--down-tint)", display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between" }}>
      <span className="body">{message}</span>
      {onRetry && (
        <button type="button" className="btns" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="sub" aria-live="polite" style={{ padding: "24px 0" }}>
      {label}
    </p>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label htmlFor={id} style={{ fontSize: 13, fontWeight: 600 }}>
        {label}
      </label>
      {children(id)}
      {hint && <span className="sub">{hint}</span>}
    </div>
  );
}

/** A side panel for forms. Escape or the backdrop closes it; focus moves inside. */
export function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  // Runs once per opening: a parent re-render must not steal focus back to the first field.
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, []);
  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className="drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
          <h2 className="d" style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>
            {title}
          </h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Empty({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="hero" style={{ display: "flex", flexDirection: "column", gap: 12, alignItems: "flex-start" }}>
      <span className="d" style={{ fontSize: 28, fontWeight: 800 }}>
        {title}
      </span>
      {children && <span className="body">{children}</span>}
      {actions && <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 4 }}>{actions}</div>}
    </div>
  );
}
