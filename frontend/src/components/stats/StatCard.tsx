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
      className="relative overflow-hidden rounded-xl border border-white/[0.06] bg-[#111318] p-5 animate-fade-up"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        className="absolute -top-8 -right-8 w-32 h-32 rounded-full blur-3xl"
        style={{ background: color, opacity: 0.06 }}
      />
      <p className="text-[11px] font-medium text-white/35 uppercase tracking-wider">{title}</p>
      <p className="text-3xl font-bold text-white mt-2 tracking-tight">{value}</p>
      {subtitle && <p className="text-[11px] text-white/25 mt-1">{subtitle}</p>}
    </div>
  );
}
