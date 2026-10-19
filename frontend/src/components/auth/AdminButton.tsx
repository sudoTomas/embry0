import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { useIsAdmin } from "@/stores/authStore";
import { cn } from "@/lib/utils";

/**
 * A button for an action the API reserves for admins. For an operator it
 * stays in place but disabled, with the reason on hover, the way the
 * Credentials page gates its admin actions.
 */
export const AdminButton = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ disabled, className, ...props }, ref) => {
    const isAdmin = useIsAdmin();
    if (isAdmin) return <Button ref={ref} disabled={disabled} className={className} {...props} />;
    return (
      <Tooltip content="Admin scope required">
        <Button ref={ref} {...props} disabled className={cn(className, "pointer-events-none")} />
      </Tooltip>
    );
  },
);
AdminButton.displayName = "AdminButton";
