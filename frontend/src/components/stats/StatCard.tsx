interface StatCardProps {
  title: string;
  value: string;
  subtitle?: string;
  color?: string;
  delay?: number;
  className?: string;
}

export function StatCard({ title, value, subtitle, color = "#3b82f6", delay = 0 }: StatCardProps) {
  return (
    <div
      className="legion-card relative overflow-hidden p-5 animate-fade-up"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        className="absolute -top-8 -right-8 w-36 h-36 rounded-full blur-3xl"
        style={{ background: color, opacity: 0.10 }}
      />
      <p className="text-[11px] font-medium text-white/35 uppercase tracking-wider">{title}</p>
      <p
        className="text-4xl font-bold mt-2 tracking-tight"
        style={{ color }}
      >
        {value}
      </p>
      {subtitle && <p className="text-[11px] text-white/25 mt-1">{subtitle}</p>}
    </div>
  );
}
