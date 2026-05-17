import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import React from "react";
import { PosteriorAuditPanel } from "./components/PosteriorAuditPanel";

describe("PosteriorAuditPanel", () => {
  it("renders deviations and contested spans", () => {
    const payload = {
      task_deviations: [
        {
          task_id: "t-1",
          row_index: 0,
          span: "Apple",
          current_type: "technology",
          prior_dominant_type: "organization",
          prior_distribution: { organization: 12 },
          prior_total: 12,
        },
      ],
      contested_spans: [
        {
          span: "Microsoft",
          prior_total: 30,
          prior_distribution: { organization: 13, project: 12, technology: 5 },
          top_share: 0.43,
          runner_up_share: 0.40,
        },
      ],
    };
    const html = renderToString(
      React.createElement(PosteriorAuditPanel, {
        projectId: "p",
        initialPayload: payload,
        onSendToHr: vi.fn(),
        onDeclareCanonical: vi.fn(),
      })
    );
    expect(html).toContain("Apple");
    expect(html).toContain("technology");
    expect(html).toContain("organization");
    expect(html).toContain("Microsoft");
  });
});
