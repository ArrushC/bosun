import { useState } from "react";

import { Md } from "@/components/ui/display";
import { Segmented, Textarea } from "@/components/ui/inputs";
import { cn } from "@/lib/utils";

export function MarkdownEditor({ value, onChange, placeholder, rows = 6, label }: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  label: string;
}) {
  const [mode, setMode] = useState<"write" | "preview">("write");
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-text-2">{label}</span>
        <Segmented
          size="xs"
          value={mode}
          onChange={setMode}
          options={[
            { value: "write", label: "Write" },
            { value: "preview", label: "Preview" },
          ]}
        />
      </div>
      {mode === "write" ? (
        <Textarea
          aria-label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          rows={rows}
          className="font-sans"
        />
      ) : (
        <div className={cn("min-h-24 rounded-control border border-line bg-sunken px-3 py-2")}>
          {value.trim() ? <Md>{value}</Md> : <p className="text-sm text-text-3">Nothing to preview.</p>}
        </div>
      )}
    </div>
  );
}
