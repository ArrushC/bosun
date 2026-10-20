import { GitHubSection } from "@/components/integrations/GitHubSection";
import { WorkspacesSection } from "@/components/integrations/WorkspacesSection";
import { PageHeader } from "@/components/layout/PageHeader";

export function IntegrationsPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Integrations" subtitle="Where Bosun finds work, and where agents may run." />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-16 px-8 pt-2 pb-16">
          <WorkspacesSection />
          <GitHubSection />
        </div>
      </div>
    </div>
  );
}
