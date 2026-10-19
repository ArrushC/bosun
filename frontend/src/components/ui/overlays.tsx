import { Dialog as DialogPrimitive, DropdownMenu as Menu, Popover as PopoverPrimitive, Tooltip as TooltipPrimitive } from "radix-ui";
import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// -- Dialog ---------------------------------------------------------------------------
export function Dialog({ open, onOpenChange, title, description, children, footer, size = "md" }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const width = { sm: "max-w-md", md: "max-w-xl", lg: "max-w-3xl", xl: "max-w-5xl" }[size];
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[rgb(6_9_15/0.62)] backdrop-blur-[2px]" />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-[8vh] left-1/2 z-50 flex max-h-[84vh] w-[calc(100vw-32px)] -translate-x-1/2 flex-col rounded-panel border border-line bg-panel shadow-pop focus:outline-none",
            width,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line-soft px-5 pt-4 pb-3">
            <div className="min-w-0">
              <DialogPrimitive.Title className="text-md font-semibold">{title}</DialogPrimitive.Title>
              {description ? (
                <DialogPrimitive.Description className="mt-0.5 text-sm text-text-2">{description}</DialogPrimitive.Description>
              ) : (
                <DialogPrimitive.Description className="sr-only">{typeof title === "string" ? title : "Dialog"}</DialogPrimitive.Description>
              )}
            </div>
            <DialogPrimitive.Close className="rounded-control p-1 text-text-3 hover:bg-raised hover:text-text" aria-label="Close">
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex items-center justify-end gap-2 border-t border-line-soft px-5 py-3">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// -- Sheet (right-side panel) ------------------------------------------------------------
export function Sheet({ open, onOpenChange, children, label, width = 640 }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  label: string;
  width?: number;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-30 bg-[rgb(6_9_15/0.35)] data-[state=open]:animate-[fade-in_120ms_ease-out]" />
        <DialogPrimitive.Content
          aria-describedby={undefined}
          style={{ width: `min(${width}px, 100vw)` }}
          className="fixed top-0 right-0 z-30 flex h-full flex-col border-l border-line bg-panel shadow-pop focus:outline-none data-[state=open]:animate-[sheet-in_160ms_cubic-bezier(.2,.8,.2,1)]"
        >
          <DialogPrimitive.Title className="sr-only">{label}</DialogPrimitive.Title>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

// -- Tooltip ---------------------------------------------------------------------------
export function Tip({ label, children, side = "top" }: { label: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <TooltipPrimitive.Root delayDuration={250}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          sideOffset={6}
          className="z-50 max-w-xs rounded-[5px] border border-line bg-raised px-2 py-1 text-xs text-text shadow-pop"
        >
          {label}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export const TooltipProvider = TooltipPrimitive.Provider;

// -- Dropdown menu ------------------------------------------------------------------------
export const DropdownMenu = Menu.Root;
export const DropdownTrigger = Menu.Trigger;

export function DropdownContent({ children, align = "end" }: { children: ReactNode; align?: "start" | "end" | "center" }) {
  return (
    <Menu.Portal>
      <Menu.Content
        align={align}
        sideOffset={6}
        className="z-50 min-w-44 rounded-[8px] border border-line bg-panel p-1 shadow-pop"
      >
        {children}
      </Menu.Content>
    </Menu.Portal>
  );
}

export function DropdownItem({ children, onSelect, danger, disabled }: { children: ReactNode; onSelect?: () => void; danger?: boolean; disabled?: boolean }) {
  return (
    <Menu.Item
      disabled={disabled}
      onSelect={onSelect}
      className={cn(
        "flex h-7 cursor-pointer items-center gap-2 rounded-[5px] px-2 text-sm text-text outline-none select-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40 data-[highlighted]:bg-raised [&_svg]:size-3.5 [&_svg]:text-text-2",
        danger && "text-st-fail [&_svg]:text-st-fail",
      )}
    >
      {children}
    </Menu.Item>
  );
}

export const DropdownSeparator = () => <Menu.Separator className="my-1 h-px bg-line-soft" />;

export function DropdownLabel({ children }: { children: ReactNode }) {
  return <Menu.Label className="px-2 pt-1.5 pb-1 text-2xs text-text-3">{children}</Menu.Label>;
}

export function DropdownCheckbox({ checked, onCheckedChange, children }: { checked: boolean; onCheckedChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <Menu.CheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(e) => e.preventDefault()}
      className="flex h-7 cursor-pointer items-center gap-2 rounded-[5px] px-2 text-sm text-text outline-none select-none data-[highlighted]:bg-raised"
    >
      <span className={cn("flex size-3.5 items-center justify-center rounded-[3px] border border-line", checked && "border-accent bg-accent")}>
        {checked && <span className="size-1.5 rounded-[1px] bg-accent-fg" />}
      </span>
      {children}
    </Menu.CheckboxItem>
  );
}

// -- Popover --------------------------------------------------------------------------
export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export function PopoverContent({ children, className, align = "start" }: { children: ReactNode; className?: string; align?: "start" | "end" | "center" }) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={6}
        className={cn("z-50 rounded-[8px] border border-line bg-panel p-2 shadow-pop focus:outline-none", className)}
      >
        {children}
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}
