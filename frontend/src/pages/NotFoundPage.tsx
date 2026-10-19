import { Compass } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/display";

export function NotFoundPage() {
  return (
    <EmptyState icon={Compass} title="This page does not exist" className="h-full" action={<Button variant="secondary" asChild><Link to="/">Back to overview</Link></Button>}>
      Check the address, or use ⌘K to jump to a task or run.
    </EmptyState>
  );
}
