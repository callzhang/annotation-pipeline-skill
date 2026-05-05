import type { ProviderConfigSnapshot, ProviderName, ProviderProfileConfig } from "./types";

export function createProviderProfile(provider: ProviderName, index: number): ProviderProfileConfig {
  if (provider === "local_cli") {
    return {
      name: `local_cli_${index}`,
      provider,
      provider_flavor: null,
      cli_kind: "codex",
      cli_binary: "codex",
      model: "gpt-5.4-mini",
      api_key_env: null,
      base_url: null,
      reasoning_effort: "none",
      permission_mode: null,
      timeout_seconds: 900,
      max_retries: null,
      concurrency_limit: null,
      no_progress_timeout_seconds: 30,
    };
  }
  if (provider === "openai_compatible") {
    return {
      name: `openai_compatible_${index}`,
      provider,
      provider_flavor: "deepseek",
      cli_kind: null,
      cli_binary: null,
      model: "deepseek-chat",
      api_key_env: "DEEPSEEK_API_KEY",
      base_url: "https://api.deepseek.com",
      reasoning_effort: null,
      permission_mode: null,
      timeout_seconds: 300,
      max_retries: null,
      concurrency_limit: null,
      no_progress_timeout_seconds: null,
    };
  }
  return {
    name: `openai_responses_${index}`,
    provider,
    provider_flavor: null,
    cli_kind: null,
    cli_binary: null,
    model: "gpt-5.4-mini",
    api_key_env: "OPENAI_API_KEY",
    base_url: "https://api.openai.com/v1",
    reasoning_effort: "medium",
    permission_mode: null,
    timeout_seconds: 300,
    max_retries: null,
    concurrency_limit: null,
    no_progress_timeout_seconds: null,
  };
}

export function providerConfigPayload(snapshot: ProviderConfigSnapshot) {
  return {
    profiles: snapshot.profiles,
    targets: snapshot.targets,
    limits: snapshot.limits,
  };
}

export function profileTitle(profile: ProviderProfileConfig): string {
  const providerDetail = profile.provider === "local_cli" ? profile.cli_kind : profile.provider_flavor;
  return `${profile.name} · ${profile.provider}${providerDetail ? `/${providerDetail}` : ""} · ${profile.model}`;
}

export function profileStatusLabel(snapshot: ProviderConfigSnapshot, profileName: string): string {
  return snapshot.diagnostics[profileName]?.status ?? "unknown";
}
