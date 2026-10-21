import type { Organization } from "@/api/orgs";
import { credentialLabel, envTokenKey } from "@/lib/orgs";
import type { Credential } from "@/lib/types/credentials";
import { cn } from "@/lib/utils";

/** The account an organization acts with. Identifiers (a credential's label,
 * the environment variable a token comes from) read in mono; the words
 * around them do not. `nowrap` keeps it on one line in a table cell; without
 * it a long identifier breaks inside a phone-width line instead of pushing
 * the page sideways. */
export function OrgCredential({
  org,
  credentials,
  nowrap = false,
}: {
  org: Organization;
  credentials: readonly Credential[] | undefined;
  nowrap?: boolean;
}) {
  if (org.credential_id) {
    return <span className={cn("font-mono text-xs", !nowrap && "break-all")}>{credentialLabel(org, credentials)}</span>;
  }
  const key = envTokenKey(org);
  return (
    <span className={cn(nowrap && "whitespace-nowrap")}>
      Environment token
      {key && <span className={cn("ml-1.5 font-mono text-xs text-fg-secondary", !nowrap && "break-all")}>{key}</span>}
    </span>
  );
}
