import { describe, expect, it } from "vitest";
import { createProviderProfile, profileTitle, providerConfigPayload } from "./providers";
import type { ProviderConfigSnapshot } from "./types";

const snapshot: ProviderConfigSnapshot = {
  config_valid: true,
  profiles: [
    {
      name: "local_codex",
      provider: "local_cli",
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
    },
  ],
  targets: { annotation: "local_codex", qc: "local_codex" },
  limits: { local_cli_global_concurrency: 4 },
  diagnostics: {
    local_codex: {
      status: "ok",
      checks: [{ id: "cli_binary_found", status: "ok", message: "codex is available" }],
    },
  },
};

describe("provider config helpers", () => {
  it("creates explicit provider profiles for selected provider kinds", () => {
    expect(createProviderProfile("openai_compatible", 2)).toMatchObject({
      name: "openai_compatible_2",
      provider: "openai_compatible",
      provider_flavor: "deepseek",
      model: "deepseek-chat",
      api_key_env: "DEEPSEEK_API_KEY",
    });
  });

  it("builds a compact save payload without diagnostics", () => {
    expect(providerConfigPayload(snapshot)).toEqual({
      profiles: snapshot.profiles,
      targets: snapshot.targets,
      limits: snapshot.limits,
    });
  });

  it("formats profile titles for operator scanning", () => {
    expect(profileTitle(snapshot.profiles[0])).toBe("local_codex · local_cli/codex · gpt-5.4-mini");
  });
});
