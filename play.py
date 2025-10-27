import json
import re
import argparse
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Frame

# ----------------------
# Configurable constants
# ----------------------
ENTRY_URL = "https://your-host-1.example.com"  # TODO: 替换为第1页入口地址
CLICK_WAIT_MS = 5000
STEP_DELAY_MS = 5000
PAGE_TURN_DELAY_MS = 1000
PAGES_TO_CAPTURE = 3
TAB_TEXTS = ['投标文件（无价格标）', '投标文件(无价格标)']
SECTION_TEXTS = ['资格审查资料', '资格审查材料', '资格性审查', '资格审查', '资格文件', '资格材料']
HEADLESS = False  # 如需无头运行改为 True（也可用 --headless 参数）


# ----------------------
# Helpers
# ----------------------

def norm(text: str) -> str:
    """Normalize text to improve fuzzy matching."""
    return re.sub(r"[，,、。：:（）()\[\]【】《》·•\-——\s]", "", text or "").strip()


def now_stamp() -> str:
    return datetime.now().isoformat(timespec="seconds").replace(":", "-")


def click_if_visible(locator) -> bool:
    try:
        if locator and locator.is_visible():
            locator.click()
            return True
    except Exception:
        pass
    return False


def expect_popup_or_same_page(page: Page, click_fn, wait_state: str = "domcontentloaded", timeout: int = 8000) -> Page:
    """Click an element that may open in popup or same page.
    Returns the new Page if popup happened, or the same page after navigation.
    """
    try:
        with page.expect_popup(timeout=timeout) as popup_info:
            click_fn()
        new_page = popup_info.value
        new_page.wait_for_load_state(wait_state)
        return new_page
    except PWTimeout:
        # No popup; probably same-page navigation
        click_fn()  # in case the click didn't occur within the context manager
        try:
            page.wait_for_load_state(wait_state, timeout=timeout)
        except Exception:
            pass
        return page


def find_pdf_frame(page: Page) -> Optional[Frame]:
    """Find a frame that looks like PDF.js (has #viewer and #outerContainer)."""
    for f in page.frames:
        try:
            if f.locator("#viewer").count() and f.locator("#outerContainer").count():
                return f
        except Exception:
            continue
    # Some implementations render PDF in main frame
    if page.locator("#viewer").count():
        return page.main_frame
    return None


def ensure_outline_open(frame: Frame) -> None:
    try:
        if frame.locator("#outlineView .outlineItem a").count() > 0:
            return
        for sel in ["#viewOutline", "[aria-controls='outlineView']", "#sidebarToggle"]:
            loc = frame.locator(sel)
            if loc.count():
                try:
                    loc.first.click()
                except Exception:
                    pass
    except Exception:
        pass


def find_outline_anchor_index(frame: Frame) -> Optional[int]:
    try:
        anchors = frame.locator("#outlineView .outlineItem a")
        n = anchors.count()
        for i in range(n):
            t = anchors.nth(i).inner_text().strip()
            if any(norm(key) in norm(t) for key in SECTION_TEXTS):
                return i
    except Exception:
        pass
    return None


def get_current_page(frame: Frame) -> int:
    try:
        return int(frame.locator("#pageNumber").input_value())
    except Exception:
        return 1


def get_total_pages(frame: Frame) -> Optional[int]:
    try:
        text = frame.locator("#numPages").inner_text()
        m = re.findall(r"\d+", text)
        return int(m[-1]) if m else None
    except Exception:
        return None


def go_to_page(frame: Frame, num: int) -> None:
    try:
        # Prefer using PDFViewerApplication if present
        has_app = frame.evaluate("() => typeof PDFViewerApplication !== 'undefined'")
        if has_app:
            frame.evaluate(f"() => PDFViewerApplication.page = {num}")
            return
    except Exception:
        pass
    try:
        frame.locator(f"#viewer .page[data-page-number='{num}']").scroll_into_view_if_needed()
    except Exception:
        pass


def read_page_text(frame: Frame, num: int) -> str:
    try:
        tl = frame.locator(f"#viewer .page[data-page-number='{num}'] .textLayer")
        return (tl.inner_text() or "").strip()
    except Exception:
        return ""


def is_only_page_num(text: str) -> bool:
    return bool(re.match(r"^第\s*\d+\s*页$", text.strip()))


# ----------------------
# Main flow
# ----------------------

def run(entry_url: str, headless: bool, pages_to_capture: int) -> None:
    results: List[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page1 = context.new_page()

        # 进入第1页
        page1.goto(entry_url)
        page1.wait_for_load_state("networkidle")

        # 可选：点击第一个项目（如存在）
        try:
            proj = page1.locator(".recent-visits .el-menu-item").first
            if proj.is_visible():
                proj.click()
                page1.wait_for_timeout(STEP_DELAY_MS)
        except Exception:
            pass

        # 步骤1：点击“评标”
        try:
            # 优先精准文本匹配
            btn = page1.locator(".steps-btn", has_text="评标").first
            if not click_if_visible(btn):
                # 退化：全页文本查找
                page1.get_by_text("评标", exact=False).first.click()
            page1.wait_for_timeout(STEP_DELAY_MS)
        except Exception:
            pass

        # 步骤2：点击“进入评标会” -> 可能新开标签
        def click_enter_eval():
            try:
                page1.get_by_text("进入评标会", exact=False).first.click()
            except Exception:
                page1.locator("button", has_text="进入评标会").first.click()

        page2 = expect_popup_or_same_page(page1, click_enter_eval)

        # 步骤3：在 page2 点击“文件”
        try:
            try:
                page2.get_by_text("文件", exact=True).first.click()
            except Exception:
                # 尝试 class + 文本
                loc = page2.locator(".cursor-pointer", has_text="文件").first
                if loc.is_visible():
                    loc.click()
            page2.wait_for_timeout(STEP_DELAY_MS)
        except Exception:
            pass

        # 步骤4：在 page2 点击“投标文件” -> 可能新开标签
        def click_bid_file():
            try:
                page2.locator("button:has-text('投标文件')").first.click()
            except Exception:
                page2.get_by_text("投标文件", exact=True).first.click()

        page3 = expect_popup_or_same_page(page2, click_bid_file)
        page3.wait_for_timeout(2000)

        # 步骤5：在 page3 遍历投标单位并抓取 PDF 文本
        try:
            aside = page3.locator(".app-aside")
            aside.wait_for(state="visible", timeout=20000)
        except Exception:
            print("未找到投标单位侧栏 .app-aside")

        items = page3.locator(".app-aside ul li")
        count = items.count()
        if count == 0:
            # 退化：尽量宽松
            items = page3.locator("ul li")
            count = min(items.count(), 30)

        for i in range(count):
            try:
                li = items.nth(i)
                if not li.is_visible():
                    continue
                bidder = (li.inner_text() or "").strip()

                li.click()
                page3.wait_for_timeout(CLICK_WAIT_MS)

                # 切到 “投标文件（无价格标）” Tab
                tabs = page3.locator(".el-tabs__item")
                tab_index = None
                tcount = tabs.count()
                for j in range(tcount):
                    t = (tabs.nth(j).inner_text() or "").strip()
                    if any(norm(tt) in norm(t) for tt in TAB_TEXTS) or ("无价格" in t):
                        tab_index = j
                        break
                if tab_index is not None:
                    tabs.nth(tab_index).click()
                    page3.wait_for_timeout(CLICK_WAIT_MS)

                # 定位 PDF frame
                frame = find_pdf_frame(page3)
                if not frame:
                    results.append({"bidderName": bidder, "error": "PDF未加载"})
                    continue

                ensure_outline_open(frame)
                page3.wait_for_timeout(300)

                anchor_idx = find_outline_anchor_index(frame)
                if anchor_idx is None:
                    results.append({"bidderName": bidder, "error": "未找到目录：资格审查*"})
                    continue

                frame.locator("#outlineView .outlineItem a").nth(anchor_idx).click()
                page3.wait_for_timeout(CLICK_WAIT_MS)

                start_page = get_current_page(frame)
                total = get_total_pages(frame) or 9999

                texts: List[str] = []
                for k in range(pages_to_capture):
                    num = min(start_page + k, total)
                    go_to_page(frame, num)
                    page3.wait_for_timeout(PAGE_TURN_DELAY_MS)

                    txt = read_page_text(frame, num)
                    if not txt or len(txt) < 20 or is_only_page_num(txt):
                        page3.wait_for_timeout(CLICK_WAIT_MS)
                        txt = read_page_text(frame, num)
                    if txt:
                        texts.append(txt)

                results.append({
                    "bidderName": bidder,
                    "section": "资格审查",
                    "startPage": start_page,
                    "endPage": min(start_page + pages_to_capture - 1, total),
                    "pagesCaptured": pages_to_capture,
                    "content": "\n".join(texts).strip(),
                })
            except Exception as e:
                results.append({"bidderName": f"item-{i}", "error": str(e)})

        # 输出结果
        out = Path(f"资格审查资料_{now_stamp()}.json")
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"完成，已写入 {out}")

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="评标三页自动化抓取")
    parser.add_argument("--url", default=ENTRY_URL, help="第1页入口地址")
    parser.add_argument("--headless", action="store_true", help="无头运行")
    parser.add_argument("--pages", type=int, default=PAGES_TO_CAPTURE, help="从定位页起抓取页数")
    args = parser.parse_args()

    run(entry_url=args.url, headless=args.headless or HEADLESS, pages_to_capture=args.pages)
