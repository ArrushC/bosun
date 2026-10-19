/** Plain-language explanations and next steps for run failure kinds. */
export const ERROR_ADVICE: Record<string, { title: string; fix: string }> = {
  auth_required: {
    title: "Claude is not signed in",
    fix: "Run `claude auth login` in a terminal (or fix the provider in Settings), then retry the run.",
  },
  usage_limit: {
    title: "Your Claude usage limit was reached",
    fix: "Wait for the limit window to reset, then resume or retry. Bosun never switches to a billed provider on its own.",
  },
  rate_limited: { title: "Claude is rate limiting requests", fix: "Retry in a few minutes." },
  billing: { title: "Billing problem with the selected provider", fix: "Check the account behind the selected provider, then retry." },
  billing_guard: {
    title: "Stopped: the session was not using the selected provider",
    fix: "Claude Code reported a different credential than the one you selected. Remove stray API keys or provider settings from the workspace's .claude/settings.json and environment, then retry.",
  },
  sandbox_unavailable: {
    title: "The shell sandbox is not available",
    fix: "Sandboxed shell commands are on in Settings, but Claude Code could not start its sandbox. On Linux install bubblewrap and socat (for example `sudo dnf install bubblewrap socat`), or turn the sandbox off in Settings. Commands never run unsandboxed while it is on.",
  },
  cli_missing: { title: "Claude Code CLI not found", fix: "Install Claude Code or set BOSUN_CLAUDE_CLI_PATH in Bosun's .env file." },
  process_crash: { title: "The agent process exited unexpectedly", fix: "Retry the run. If it keeps happening, check the backend log." },
  timeout: { title: "The session ran out of time", fix: "Send a follow-up to continue, or raise the session time limit in Settings." },
  max_turns: { title: "The agent reached its turn limit", fix: "Send a follow-up to continue, or raise the turn limit when launching." },
  invalid_request: { title: "The request was rejected", fix: "Check the error details; adjust the task or plan and retry." },
  server_error: { title: "Claude returned a server error", fix: "Retry the run." },
  workspace: { title: "The workspace is unavailable", fix: "Make sure the directory and the run's worktree still exist, then retry or start a new run." },
  unknown: { title: "The run failed", fix: "Review the agent transcripts for details, then retry." },
};

export function adviceFor(kind: string | null | undefined) {
  return ERROR_ADVICE[kind ?? "unknown"] ?? ERROR_ADVICE.unknown;
}
