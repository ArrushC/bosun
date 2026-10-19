import { AlertOctagon, CheckCircle2 } from "lucide-react";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

interface Point {
  date: string;
  completed: number;
  failed: number;
  done: number;
}

function label(date: string) {
  return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Runs finished per day. Status colours are paired with a texture (failed runs
 * are hatched) and labelled legend entries, so the series never depend on
 * telling green from red.
 */
export function ThroughputChart({ data }: { data: Point[] }) {
  const totals = data.reduce((acc, d) => ({ completed: acc.completed + d.completed, failed: acc.failed + d.failed, done: acc.done + d.done }), {
    completed: 0,
    failed: 0,
    done: 0,
  });
  return (
    <figure>
      <figcaption className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-2">
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-[2px] bg-st-ok" />
          <CheckCircle2 className="size-3 text-text-3" /> Completed {totals.completed}
        </span>
        <span className="flex items-center gap-1.5">
          <svg className="size-2.5" viewBox="0 0 10 10" aria-hidden>
            <rect width="10" height="10" rx="2" fill="var(--st-fail)" opacity="0.35" />
            <path d="M-2 8 L8 -2 M2 12 L12 2" stroke="var(--st-fail)" strokeWidth="2" />
          </svg>
          <AlertOctagon className="size-3 text-text-3" /> Failed {totals.failed}
        </span>
        <span className="ml-auto text-text-3">{totals.done} tasks marked done</span>
      </figcaption>
      <div className="h-32" role="img" aria-label={`Runs over 14 days: ${totals.completed} completed, ${totals.failed} failed`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }} barCategoryGap={3}>
            <defs>
              <pattern id="hatch-failed" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="5" height="5" fill="color-mix(in srgb, var(--st-fail) 35%, transparent)" />
                <line x1="0" y1="0" x2="0" y2="5" stroke="var(--st-fail)" strokeWidth="2.5" />
              </pattern>
            </defs>
            <XAxis dataKey="date" tickFormatter={label} interval={3} tick={{ fill: "var(--text-3)", fontSize: 10.5 }} axisLine={{ stroke: "var(--line-soft)" }} tickLine={false} />
            <YAxis allowDecimals={false} tick={{ fill: "var(--text-3)", fontSize: 10.5 }} axisLine={false} tickLine={false} width={24} />
            <Tooltip
              cursor={{ fill: "var(--raised)" }}
              content={({ active, payload, label: l }) =>
                active && payload?.length ? (
                  <div className="rounded-[6px] border border-line bg-panel px-2.5 py-1.5 text-xs shadow-pop">
                    <p className="mb-0.5 font-semibold text-text">{label(String(l))}</p>
                    <p className="text-text-2">Completed: {(payload[0].payload as Point).completed}</p>
                    <p className="text-text-2">Failed: {(payload[0].payload as Point).failed}</p>
                    <p className="text-text-3">Tasks done: {(payload[0].payload as Point).done}</p>
                  </div>
                ) : null
              }
            />
            <Bar dataKey="completed" stackId="runs" fill="var(--st-ok)" stroke="var(--panel)" strokeWidth={1} radius={[2, 2, 0, 0]} isAnimationActive={false} />
            <Bar dataKey="failed" stackId="runs" fill="url(#hatch-failed)" stroke="var(--panel)" strokeWidth={1} radius={[2, 2, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
