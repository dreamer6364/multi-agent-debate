import { expect, test } from "@playwright/test";

// E2E-WEB-01（TEST_PLAN §8）：注册 → 登录 → 建辩题（使用内置辩题）→ 发起精简赛
// → 直播页出现发言流与终态横幅 → 归档回看 → 导出可下载。
// 后端为 FakeLLM 开发档（scripts/dev_uvicorn.py），比赛秒级完成。

test("E2E-WEB-01 注册到归档导出的全链路", async ({ page }) => {
  const username = `e2e_${Date.now()}`;

  // 1) 注册即登录
  await page.goto("/login");
  await page.getByRole("tab", { name: "注册" }).click();
  const panel = page.getByRole("tabpanel", { name: "注册" });
  await panel.getByRole("textbox").nth(0).fill(username);
  await panel.getByRole("textbox").nth(1).fill("password8");
  await panel.getByRole("button", { name: /注册并登录|创建我的席位/ }).click();
  await expect(page).toHaveURL(/\/topics/);
  await expect(page.getByText("内置").first()).toBeVisible(); // 辩题库已加载

  // 2) 发起精简赛
  await page.getByRole("button", { name: "发起比赛" }).first().click();
  const modal = page.getByRole("dialog");
  await modal.locator(".ant-select").first().click();
  await page.getByText("精简赛制（5 环节）").click();
  await modal.getByRole("button", { name: /开\s*始/ }).click();
  await expect(page).toHaveURL(/\/debates\/\d+/, { timeout: 15_000 });

  // 3) 直播页：等待终态（FakeLLM 秒级完成；SSE 或轮询兜底都应到达）
  await expect(page.getByText(/本场胜方：|本场平局/).first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("已结束")).toBeVisible();
  await expect(page.getByText(/LLM 用量/)).toBeVisible();
  // 评分表渲染
  await expect(page.getByRole("columnheader", { name: "评委" })).toBeVisible();
  // 发言流有内容
  await expect(page.getByTestId("speech-card").first()).toBeVisible();

  // 4) 归档回看
  await page.getByRole("link", { name: /归\s*档/ }).first().click();
  await expect(page).toHaveURL(/\/debates$/);
  await expect(page.getByText("已结束").first()).toBeVisible();
  await page.getByRole("cell", { name: /人工智能时代/ }).first().click();
  await expect(page).toHaveURL(/\/debates\/\d+/);
  await expect(page.getByTestId("speech-card").first()).toBeVisible(); // 回看完整实录

  // 5) 导出可下载
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: /导出实录/ }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/\.md$/);
});
