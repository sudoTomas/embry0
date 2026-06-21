import { useEffect, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type AgentNotification,
  type AgentNotificationLevel,
  fetchNotifications,
  markAllNotificationsRead,
} from "@/api/agent";
import { Button } from "@/components/ui/Button";
import { cn, formatDate } from "@/lib/utils";

const NOTIFICATIONS_QUERY_KEY = ["agent", "notifications"] as const;

const LEVEL_DOT: Record<AgentNotificationLevel, string> = {
  info: "bg-cyan-500",
  success: "bg-success",
  warning: "bg-warning",
  error: "bg-destructive",
};

interface NotificationRowProps {
  notification: AgentNotification;
}

function NotificationBody({ notification }: NotificationRowProps) {
  const dot = LEVEL_DOT[notification.level ?? "info"];
  return (
    <div className="flex items-start gap-2">
      <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dot)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cn(
              "truncate text-sm",
              notification.read ? "text-white/60" : "font-medium text-white/90",
            )}
          >
            {notification.title}
          </span>
          <span className="shrink-0 text-[10px] uppercase tracking-wide text-white/40">
            {formatDate(notification.ts)}
          </span>
        </div>
        {notification.body && (
          <p className="mt-0.5 line-clamp-2 text-xs text-white/50">{notification.body}</p>
        )}
      </div>
    </div>
  );
}

function NotificationRow({ notification }: NotificationRowProps) {
  const baseRow =
    "block border-t border-white/[0.04] px-3 py-2 first:border-t-0 hover:bg-white/[0.03]";
  if (notification.href) {
    return (
      <Link
        to={notification.href}
        data-testid={`notification-link-${notification.id}`}
        className={baseRow}
      >
        <div data-testid={`notification-row-${notification.id}`}>
          <NotificationBody notification={notification} />
        </div>
      </Link>
    );
  }
  return (
    <div data-testid={`notification-row-${notification.id}`} className={baseRow}>
      <NotificationBody notification={notification} />
    </div>
  );
}

export function NotificationsCenter() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const queryClient = useQueryClient();

  const { data: notifications = [] } = useQuery({
    queryKey: NOTIFICATIONS_QUERY_KEY,
    queryFn: fetchNotifications,
    refetchInterval: 30_000,
  });

  const unread = notifications.filter((n) => !n.read).length;

  const markAll = useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_QUERY_KEY });
    },
  });

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        aria-label="Notifications"
        onClick={() => setOpen((v) => !v)}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span
            data-testid="notifications-unread-count"
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Button>
      {open && (
        <div
          data-testid="notifications-dropdown"
          className="absolute right-0 top-full z-40 mt-2 w-80 overflow-hidden rounded-md border border-white/[0.06] bg-[#0c1015] shadow-[0_8px_24px_rgba(0,0,0,0.4)]"
        >
          <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-white/70">
              Notifications
            </span>
            <Button
              variant="ghost"
              size="sm"
              disabled={unread === 0 || markAll.isPending}
              onClick={() => markAll.mutate()}
            >
              Mark all read
            </Button>
          </div>
          {notifications.length === 0 ? (
            <div
              data-testid="notifications-empty"
              className="px-3 py-6 text-center text-xs text-white/40"
            >
              No notifications yet
            </div>
          ) : (
            <div className="max-h-96 overflow-y-auto">
              {notifications.map((n) => (
                <NotificationRow key={n.id} notification={n} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
