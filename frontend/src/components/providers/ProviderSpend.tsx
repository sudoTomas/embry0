import { useProviderUsage } from "@/hooks/useProvider";
import { formatDate } from "@/lib/utils";

const usd = (v: number | null | undefined) => (v == null ? "—" : `$${v.toFixed(2)}`);

/** Providers whose spend can be read with an ordinary key (GET /config/providers/{name}/usage). */
const SPEND_PROVIDERS = new Set(["openrouter"]);

/**
 * What the provider says its key has spent, its limit, and the account's credit, as the
 * provider reports it: billing truth, unlike the catalogue's list prices.
 */
export function ProviderSpend({ provider }: { provider: string }) {
  const usage = useProviderUsage(provider, SPEND_PROVIDERS.has(provider));
  const data = usage.data;
  if (!data || data.status === "not_configured") return null;
  if (data.status !== "ok" || !data.key) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="provider-spend">
        Spend: could not be read ({data.detail ?? data.status})
      </p>
    );
  }
  const { key, account } = data;
  return (
    <div className="space-y-0.5 text-xs text-muted-foreground" data-testid="provider-spend">
      <p>
        Spent {usd(key.spend_today_usd)} today · {usd(key.spend_week_usd)} this week · {usd(key.spend_month_usd)} this
        month · {usd(key.spend_total_usd)} in all
      </p>
      {key.limit_usd != null && (
        <p>
          Key limit {usd(key.limit_usd)}
          {key.limit_reset ? ` ${key.limit_reset}` : ""}, {usd(key.limit_remaining_usd)} left
          {key.limit_resets_at ? `, resets ${formatDate(key.limit_resets_at)}` : ""}
        </p>
      )}
      {account && (
        <p>
          Account credit {usd(account.remaining_usd)} left of {usd(account.credits_usd)}
        </p>
      )}
      {key.expires_at && <p>Key expires {formatDate(key.expires_at)}</p>}
    </div>
  );
}
