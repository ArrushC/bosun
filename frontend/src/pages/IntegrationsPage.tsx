import { GitHubSection } from "@/components/integrations/GitHubSection";
import { WorkspacesSection } from "@/components/integrations/WorkspacesSection";
import { PageHeader } from "@/components/layout/PageHeader";

export function IntegrationsPage() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader title="Integrations" subtitle="Where work is discovered and where agents are allowed to operate." />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-4xl space-y-10 px-6 py-6">
          <WorkspacesSection />
          <GitHubSection />
        </div>
      </div>
    </div>
  );
}
