import { chromium } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const baseUrl = process.env.MEMORY_NER_UI_BASE_URL ?? "http://127.0.0.1:5173";
const reportPath = process.env.MEMORY_NER_UI_REPORT ?? "/tmp/annotation-memory-ner-ui-acceptance/report.json";
const projectId = process.env.MEMORY_NER_UI_PROJECT_ID ?? "memory-ner-accepted-e2e";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function kanbanColumn(page, title) {
  return page.locator(".kanban-column").filter({
    has: page.getByRole("heading", { name: new RegExp(`^${title}$`) }),
  });
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const report = {
    baseUrl,
    project_id: projectId,
    task_id: null,
    checks: [],
  };

  async function check(name, fn) {
    await fn();
    report.checks.push({ name, status: "passed" });
  }

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });

    await check("dashboard loads without api error", async () => {
      await page.getByRole("heading", { name: "Annotation Pipeline" }).waitFor();
      assert(!(await page.getByText("API error").isVisible().catch(() => false)), "dashboard shows API error");
    });

    await check("project selector exposes memory-ner project", async () => {
      await page.locator("select").first().selectOption(projectId);
      await page.waitForLoadState("networkidle");
      await page.waitForFunction(() => document.querySelector(".topbar")?.textContent?.includes("10 tasks"));
      const text = await page.locator(".topbar").innerText();
      assert(text.includes(projectId), `project selector did not expose ${projectId}: ${text}`);
      assert(text.includes("10 tasks"), `topbar did not show 10 tasks: ${text}`);
    });

    await check("kanban shows accepted column with ten cards and no hidden work", async () => {
      const acceptedColumn = kanbanColumn(page, "Accepted");
      await acceptedColumn.waitFor();
      const acceptedText = await acceptedColumn.innerText();
      const cards = await acceptedColumn.locator(".task-card").count();
      assert(acceptedText.includes("10"), `Accepted column did not show 10: ${acceptedText}`);
      assert(cards === 10, `expected 10 accepted cards, got ${cards}`);

      const boardText = await page.locator(".kanban-board").innerText();
      for (const label of ["Pending", "QC", "Human Review"]) {
        const column = kanbanColumn(page, label);
        if ((await column.count()) > 0) {
          const text = await column.first().innerText();
          const cardCount = await column.first().locator(".task-card").count();
          assert(cardCount === 0, `${label} column has hidden cards: ${text}`);
        }
      }
      assert(!boardText.includes("pending ·"), `board contains pending card evidence: ${boardText}`);
      assert(!boardText.includes("qc ·"), `board contains QC card evidence: ${boardText}`);
      assert(!boardText.includes("human_review ·"), `board contains Human Review card evidence: ${boardText}`);
    });

    await check("task drawer exposes source annotation attempts changes and feedback", async () => {
      const acceptedColumn = kanbanColumn(page, "Accepted");
      const firstAcceptedCard = acceptedColumn.locator(".task-card").first();
      const taskId = (await firstAcceptedCard.locator(".task-id").innerText()).trim();
      report.task_id = taskId;
      await firstAcceptedCard.click();
      await page.getByRole("heading", { name: taskId }).waitFor();
      const drawer = page.locator(".task-drawer");
      await drawer.getByText("Raw Source").waitFor();
      await drawer.getByText("QC Policy").waitFor();
      await drawer.getByText("Annotation Content").waitFor();
      await drawer.getByRole("heading", { name: /Attempts \(\d+\)/ }).waitFor();
      await drawer.getByRole("heading", { name: /Round Changes \(\d+\)/ }).waitFor();
      await drawer.getByRole("heading", { name: /Feedback Agreement \(\d+\)/ }).waitFor();
      const text = await drawer.innerText();
      for (const expected of ["Raw Source", "QC Policy", "Annotation Content", "Attempts", "Round Changes", "Feedback Agreement"]) {
        assert(text.includes(expected), `drawer missing ${expected}`);
      }
      assert(text.toLowerCase().includes("deepseek"), `drawer missing DeepSeek attempt evidence: ${text}`);
      assert(text.includes("accepted") || text.includes("accept"), `drawer missing accepted transition evidence: ${text}`);
    });

    await check("runtime panel shows no active or stale work and accepted queue count", async () => {
      await page.getByRole("button", { name: "Runtime" }).click();
      await page.waitForLoadState("networkidle");
      await page.getByRole("heading", { name: "Runtime" }).waitFor();
      const text = await page.locator("main").innerText();
      assert(text.includes("No active runs"), `runtime panel has active work: ${text}`);
      assert(text.includes("No stale tasks"), `runtime panel has stale work: ${text}`);
      assert(text.includes("accepted"), `runtime panel missing accepted queue key: ${text}`);
      assert(text.match(/accepted\s+10/i), `runtime panel missing accepted queue count 10: ${text}`);
    });

    await check("readiness panel shows ten accepted training-data rows", async () => {
      await page.getByRole("button", { name: "Readiness" }).click();
      await page.waitForLoadState("networkidle");
      await page.getByRole("heading", { name: "Readiness" }).waitFor();
      const text = await page.locator("main").innerText();
      assert(text.includes("Training Data"), `readiness panel missing training data section: ${text}`);
      assert(text.includes("10"), `readiness panel did not include count 10: ${text}`);
    });

    await check("providers panel exposes DeepSeek annotation and QC configuration", async () => {
      await page.getByRole("button", { name: "Providers" }).click();
      await page.waitForLoadState("networkidle");
      await page.getByRole("heading", { name: "Providers" }).waitFor();
      const text = await page.locator("main").innerText();
      assert(text.toLowerCase().includes("deepseek"), `providers panel missing DeepSeek: ${text}`);
      assert(text.includes("annotation"), `providers panel missing annotation target: ${text}`);
      assert(text.includes("qc"), `providers panel missing QC target: ${text}`);
    });

    await check("event log exposes accepted transitions", async () => {
      await page.getByRole("button", { name: "Event Log" }).click();
      await page.getByRole("heading", { name: "Event Log" }).waitFor();
      await page.locator(".event-row", { hasText: projectId }).first().waitFor();
      const text = await page.locator("main").innerText();
      assert(text.includes(projectId), `event log missing project events: ${text}`);
      assert(text.includes("accepted"), `event log missing accepted transition: ${text}`);
    });

    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf-8");
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
