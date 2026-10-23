import type { BadgeTone } from "@/components/ui/Badge";
import type { HostStatus, HostTransport } from "@/api/hosts";

export const HOST_STATUS_TONES: Readonly<Record<HostStatus, BadgeTone>> = {
  active: "success",
  unreachable: "warning",
  removed: "neutral",
};

/** What the address field asks for, per transport, in the words the server checks it against. */
export const ADDRESS_HINTS: Readonly<Record<HostTransport, string>> = {
  tailnet: "An address or MagicDNS name; empty matches the device by name.",
  ssh: "host or host:port",
  http: "host, host:port or a URL",
};
