# CNOOC Bid Qualification Extractor

This script automates collecting the "资格审查资料 → 基本情况表" from each bidder under 投标文件 and exports the results to an Excel file.

## Requirements
- Python 3.9+
- Google Chrome/Chromium auto-installed by Playwright

Install dependencies and Playwright browsers:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage
1. Run the script:

```bash
python scrape_cnooc_bid_qualification.py
```

2. A browser window opens. If not already logged in, complete login manually. The script will then:
   - Click `文件` → `投标文件`
   - Enumerate bidders on the left
   - For each bidder, open `资格审查资料` → `基本情况表`
   - Parse the visible table as key-value pairs
   - Save Excel to `资格审查_基本情况表.xlsx` (override with env var `OUTPUT_EXCEL`)

## Notes
- The site is a complex SPA; selectors may need minor adjustments. Update the `SELECTORS` dict if UI changes.
- If some bidders lack the section, they are skipped with a warning.
- If the 资料 is a file (PDF/Word) rather than HTML, this simple extractor won't parse it. You may extend `extract_key_value_table` to download and OCR if needed.
