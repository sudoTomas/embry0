import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { Credential } from "@/lib/types/credentials";
import { formatDate } from "@/lib/utils";
import { statusLabel } from "@/lib/status";

const DECRYPTION_FAILED = "decryption_failed";

function statusTone(credential: Credential): BadgeTone {
  // decryption_failed is an error even when status is still "disabled".
  if (credential.status_reason === DECRYPTION_FAILED) return "error";
  if (credential.status === "ok") return "success";
  if (credential.status === "quarantined") return "warning";
  return "neutral";
}

function statusTitle(credential: Credential): string | undefined {
  if (credential.status === "quarantined") {
    const until = credential.quarantined_until
      ? `until ${formatDate(credential.quarantined_until)}`
      : "until cleared";
    return credential.status_reason ? `${credential.status_reason} ${until}` : until;
  }
  if (credential.status === "disabled" || credential.status_reason === DECRYPTION_FAILED) {
    return credential.status_reason ?? undefined;
  }
  return undefined;
}

export function StatusBadge({ credential }: { credential: Credential }) {
  return (
    <Badge tone={statusTone(credential)} title={statusTitle(credential)}>
      {statusLabel(credential.status)}
    </Badge>
  );
}
