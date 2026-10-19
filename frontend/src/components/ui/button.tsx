import { Slot } from "radix-ui";
import { type ButtonHTMLAttributes, forwardRef } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "xs" | "sm" | "md" | "icon" | "icon-sm";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-fg font-semibold hover:bg-accent-strong disabled:opacity-50 shadow-[inset_0_1px_0_rgb(255_255_255/0.18)]",
  secondary: "bg-raised text-text border border-line hover:border-text-3 disabled:opacity-50",
  outline: "border border-line text-text-2 hover:text-text hover:border-text-3 disabled:opacity-50",
  ghost: "text-text-2 hover:text-text hover:bg-raised disabled:opacity-40",
  danger: "border border-st-fail/50 text-st-fail hover:bg-st-fail/10 disabled:opacity-50",
};

const SIZES: Record<Size, string> = {
  xs: "h-6 px-2 text-xs gap-1 rounded-[5px]",
  sm: "h-7 px-2.5 text-sm gap-1.5 rounded-control",
  md: "h-8 px-3 text-sm gap-2 rounded-control",
  icon: "h-8 w-8 rounded-control",
  "icon-sm": "h-6 w-6 rounded-[5px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "secondary", size = "md", asChild, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      ref={ref}
      type={asChild ? undefined : (type ?? "button")}
      className={cn(
        "inline-flex shrink-0 items-center justify-center whitespace-nowrap font-medium transition-colors select-none [&_svg]:size-[15px] [&_svg]:shrink-0",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    />
  );
});
