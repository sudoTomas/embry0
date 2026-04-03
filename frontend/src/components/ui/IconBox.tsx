import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

export type IconBoxSize = "sm" | "md" | "lg";

interface IconBoxProps {
  icon: LucideIcon;
  color: string;
  size?: IconBoxSize;
  className?: string;
}

const sizeClasses: Record<IconBoxSize, { box: string; icon: string }> = {
  sm: { box: "w-6 h-6 rounded-md", icon: "w-3.5 h-3.5" },
  md: { box: "w-8 h-8 rounded-lg", icon: "w-4 h-4" },
  lg: { box: "w-10 h-10 rounded-[10px]", icon: "w-5 h-5" },
};

export function IconBox({ icon: Icon, color, size = "md", className }: IconBoxProps) {
  return (
    <div
      className={cn("flex items-center justify-center shrink-0", sizeClasses[size].box, className)}
      style={{
        backgroundColor: `${color}18`,
        border: `1px solid ${color}40`,
        color,
      }}
    >
      <Icon className={sizeClasses[size].icon} />
    </div>
  );
}
