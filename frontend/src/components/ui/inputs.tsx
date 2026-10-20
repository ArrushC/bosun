import { Checkbox as CheckboxPrimitive, Switch as SwitchPrimitive } from "radix-ui";
import { Check, Search } from "lucide-react";
import { forwardRef, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const FIELD =
  "w-full rounded-control border border-line-soft bg-sunken px-3.5 text-sm text-text placeholder:text-text-3 transition-colors hover:border-text-3 focus:border-accent focus:outline-none disabled:opacity-50";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(FIELD, "h-10", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...props },
  ref,
) {
  return <textarea ref={ref} className={cn(FIELD, "min-h-28 py-2.5 leading-6", className)} {...props} />;
});

export const SearchInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function SearchInput(
  { className, ...props },
  ref,
) {
  return (
    <div className={cn("relative", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-text-3" />
      <input ref={ref} type="search" className={cn(FIELD, "h-10 pl-10")} {...props} />
    </div>
  );
});

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("flex flex-col gap-2", className)}>
      <span className="text-sm font-medium text-text">{label}</span>
      {children}
      {hint && <span className="text-xs text-text-3">{hint}</span>}
    </label>
  );
}

export function Checkbox({ checked, onCheckedChange, label, className, ...rest }: {
  checked: boolean | "indeterminate";
  onCheckedChange: (checked: boolean) => void;
  label?: string;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <CheckboxPrimitive.Root
      checked={checked}
      onCheckedChange={(v) => onCheckedChange(v === true)}
      aria-label={rest["aria-label"] ?? label}
      className={cn(
        "flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-line bg-sunken transition-colors hover:border-text-3 data-[state=checked]:border-accent data-[state=checked]:bg-accent data-[state=indeterminate]:border-accent data-[state=indeterminate]:bg-accent",
        className,
      )}
    >
      <CheckboxPrimitive.Indicator className="text-accent-fg">
        {checked === "indeterminate" ? <span className="block h-0.5 w-2 bg-current" /> : <Check className="size-3" strokeWidth={3} />}
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

export function Switch({ checked, onCheckedChange, label, disabled }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      aria-label={label}
      disabled={disabled}
      className="relative h-[22px] w-10 shrink-0 rounded-full border border-line bg-sunken transition-colors data-[state=checked]:border-accent data-[state=checked]:bg-accent disabled:opacity-50"
    >
      <SwitchPrimitive.Thumb className="block size-4 translate-x-0.5 rounded-full bg-text-2 transition-transform data-[state=checked]:translate-x-[20px] data-[state=checked]:bg-accent-fg" />
    </SwitchPrimitive.Root>
  );
}

export function NativeSelect({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(FIELD, "h-10 appearance-none bg-[length:14px] bg-[right_12px_center] bg-no-repeat pr-9", className)}
      style={{ backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238d99af' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")" }}
      {...props}
    >
      {children}
    </select>
  );
}

export function Segmented<T extends string>({ value, onChange, options, size = "sm", className }: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; title?: string }[];
  size?: "xs" | "sm";
  className?: string;
}) {
  return (
    <div role="radiogroup" className={cn("inline-flex gap-0.5 rounded-control bg-sunken p-1", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-[7px] font-medium text-text-2 transition-colors hover:text-text [&_svg]:size-4",
            size === "xs" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-sm",
            value === o.value && "bg-raised text-text shadow-[0_1px_2px_rgb(0_0_0/0.12)]",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
