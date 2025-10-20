import asyncio
import os
from typing import Dict, List, Tuple
import pandas as pd
from playwright.async_api import async_playwright, Page

START_URL = "https://purb.bid.cnooc.com.cn/web-bid-evaluation/index.html"
OUTPUT_EXCEL = os.environ.get("OUTPUT_EXCEL", "资格审查_基本情况表.xlsx")

# Configurable selectors and labels (best-effort; may need tweaks if DOM changes)
SELECTORS = {
    "file_tab_button": "text=文件",
    "bid_file_tab": "text=投标文件",
    "tbwj_iframe": "iframe[name='tbwj']",
    "bidder_list_items": "#leftTree .el-tree-node__content, .tb_left .el-tree-node__content",
    "qualification_tab": "text=资格审查资料",
    "basic_table_tab": "text=基本情况表",
    # Fallback generic table rows
    "kv_rows": "table tr",
}

async def wait_and_click(page: Page, selector: str, *, timeout: int = 20_000):
    await page.wait_for_selector(selector, state="visible", timeout=timeout)
    await page.click(selector)

async def extract_key_value_table(page: Page) -> List[Tuple[str, str]]:
    # Try to find rows with 2-4 cells and map first cell to the joined rest
    rows = await page.query_selector_all(SELECTORS["kv_rows"])
    kv_pairs: List[Tuple[str, str]] = []
    for row in rows:
        tds = await row.query_selector_all("td,th")
        if not tds:
            continue
        cells = [ (await (await td.inner_text()).strip()) for td in tds ]
        # Heuristic: first cell is key; rest joined as value
        key = cells[0].replace("\n", " ").strip("：: ")
        value = " ".join(cells[1:]).replace("\n", " ").strip()
        if key and value:
            kv_pairs.append((key, value))
    # Deduplicate while keeping order
    seen = set()
    deduped: List[Tuple[str, str]] = []
    for k, v in kv_pairs:
        if (k, v) in seen:
            continue
        seen.add((k, v))
        deduped.append((k, v))
    return deduped

async def goto_tbwj(page: Page) -> None:
    # Click 文件 → 投标文件; the app may open a new view within same SPA
    await wait_and_click(page, SELECTORS["file_tab_button"])  # 文件
    await wait_and_click(page, SELECTORS["bid_file_tab"])  # 投标文件

async def get_bidder_nodes(page: Page) -> List[Tuple[str, str]]:
    # Returns list of (name, selector) pairs to click
    await page.wait_for_timeout(1000)
    items = await page.query_selector_all(SELECTORS["bidder_list_items"])
    results: List[Tuple[str, str]] = []
    for idx, item in enumerate(items):
        name = (await item.inner_text()).strip()
        # Build a stable nth selector for clicking later
        nth_selector = f"{SELECTORS['bidder_list_items']} >> nth={idx}"
        if name:
            results.append((name, nth_selector))
    return results

async def open_basic_table(page: Page) -> None:
    # Click 资格审查资料 → 基本情况表
    await wait_and_click(page, SELECTORS["qualification_tab"])
    await wait_and_click(page, SELECTORS["basic_table_tab"])

async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to entry; assumes the user is already logged in via SSO/cookies or will log in manually
        await page.goto(START_URL, wait_until="networkidle")
        print("请在浏览器中登录系统，如已登录将自动继续…")
        # Allow user time to complete login manually if needed
        await page.wait_for_load_state("networkidle")

        await goto_tbwj(page)
        bidders = await get_bidder_nodes(page)
        print(f"检测到投标单位 {len(bidders)} 家")

        all_rows: List[Dict[str, str]] = []
        for bidder_name, bidder_selector in bidders:
            print(f"处理投标单位: {bidder_name}")
            await wait_and_click(page, bidder_selector)
            try:
                await open_basic_table(page)
            except Exception:
                # Some bidders may not have the section; skip gracefully
                print(f"警告: 未找到 {bidder_name} 的‘资格审查资料/基本情况表’，跳过")
                continue

            kv = await extract_key_value_table(page)
            row_dict: Dict[str, str] = {"投标单位": bidder_name}
            for k, v in kv:
                if k not in row_dict:
                    row_dict[k] = v
                else:
                    # If duplicate key, append
                    row_dict[k] = f"{row_dict[k]} | {v}"
            all_rows.append(row_dict)

        if not all_rows:
            print("未提取到任何数据。")
        else:
            # Normalize keys and write to Excel
            all_keys = set()
            for r in all_rows:
                all_keys.update(r.keys())
            ordered_cols = ["投标单位"] + sorted([k for k in all_keys if k != "投标单位"])
            df = pd.DataFrame(all_rows)
            df = df.reindex(columns=ordered_cols)
            df.to_excel(OUTPUT_EXCEL, index=False)
            print(f"已导出到 {OUTPUT_EXCEL}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
