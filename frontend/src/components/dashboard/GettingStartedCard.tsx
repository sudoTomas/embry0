import { Link } from "react-router";
import { FlaskConical, Plus, Settings } from "lucide-react";
import { IconBox } from "@/components/ui/IconBox";

export function GettingStartedCard() {
  return (
    <div className="legion-card p-8">
      <div className="text-center mb-8">
        <h2 className="text-xl font-bold text-white/90 mb-2">Welcome to Legion</h2>
        <p className="text-sm text-white/40 max-w-md mx-auto">
          Your autonomous agent orchestration engine is ready. Here's how to get started.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Link to="/demo" className="group">
          <div className="rounded-xl p-5 border border-orange-500/15 bg-orange-500/[0.03] hover:bg-orange-500/[0.06] transition-all">
            <IconBox icon={FlaskConical} color="#f97316" size="lg" className="mb-3" />
            <h3 className="font-semibold text-orange-400 mb-1">Explore the Demo</h3>
            <p className="text-xs text-white/40">See the execution dashboard with simulated agent data</p>
          </div>
        </Link>

        <Link to="/jobs" className="group">
          <div className="rounded-xl p-5 border border-cyan-500/15 bg-cyan-500/[0.03] hover:bg-cyan-500/[0.06] transition-all">
            <IconBox icon={Plus} color="#06b6d4" size="lg" className="mb-3" />
            <h3 className="font-semibold text-cyan-400 mb-1">Create a Job</h3>
            <p className="text-xs text-white/40">Submit an issue to the agent pipeline for autonomous resolution</p>
          </div>
        </Link>

        <Link to="/settings" className="group">
          <div className="rounded-xl p-5 border border-purple-500/15 bg-purple-500/[0.03] hover:bg-purple-500/[0.06] transition-all">
            <IconBox icon={Settings} color="#a855f7" size="lg" className="mb-3" />
            <h3 className="font-semibold text-purple-400 mb-1">Configure</h3>
            <p className="text-xs text-white/40">Set budgets, provider credentials, and pipeline defaults</p>
          </div>
        </Link>
      </div>
    </div>
  );
}
