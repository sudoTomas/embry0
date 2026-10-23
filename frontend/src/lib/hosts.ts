import type { BadgeTone } from "@/components/ui/Badge";
import type { HostStatus, HostTransport, LogSourceKind } from "@/api/hosts";

export const HOST_STATUS_TONES: Readonly<Record<HostStatus, BadgeTone>> = {
  active: "success",
  unreachable: "warning",
  removed: "neutral",
};

/** A transport as a person reads it; the stored word stays the option's value. */
export const TRANSPORT_LABELS: Readonly<Record<HostTransport, string>> = {
  tailnet: "Tailnet",
  ssh: "SSH",
  http: "HTTP",
};

/** A log source kind as its tool spells it (journald is lowercase by its own convention). */
export const LOG_SOURCE_LABELS: Readonly<Record<LogSourceKind, string>> = {
  journald: "journald",
  docker: "Docker",
  loki: "Loki",
};

/** What the address field asks for, per transport, in the words the server checks it against. */
export const ADDRESS_HINTS: Readonly<Record<HostTransport, string>> = {
  tailnet: "An address or MagicDNS name; empty matches the device by name.",
  ssh: "A host, or host:port.",
  http: "A host, host:port or a URL.",
};
