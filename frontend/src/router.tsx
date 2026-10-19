import { createBrowserRouter } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { BoardPage } from "@/pages/BoardPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { GraphPage } from "@/pages/GraphPage";
import { InboxPage } from "@/pages/InboxPage";
import { IntegrationsPage } from "@/pages/IntegrationsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { RunPage } from "@/pages/RunPage";
import { RunsPage } from "@/pages/RunsPage";
import { SettingsPage } from "@/pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "board", element: <BoardPage /> },
      { path: "inbox", element: <InboxPage /> },
      { path: "graph", element: <GraphPage /> },
      { path: "runs", element: <RunsPage /> },
      { path: "runs/:runId", element: <RunPage /> },
      { path: "integrations", element: <IntegrationsPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
