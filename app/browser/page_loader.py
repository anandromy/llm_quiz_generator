# app/browser/page_loader.py
from playwright.async_api import async_playwright

async def load_page_html(url: str, timeout: int = 15000) -> str:
    """
    Loads a JS-rendered page using Playwright and returns the fully-rendered HTML.
    timeout is in milliseconds (default 15 seconds).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        await page.goto(url, timeout=timeout)
        await page.wait_for_load_state("networkidle", timeout=timeout)

        html = await page.content()

        await page.close()
        await browser.close()

        return html
