import asyncio
from playwright.async_api import async_playwright
import importlib.metadata

async def test():
    try:
        version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    print(f"Playwright version: {version}")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        locator = page.locator("div")
        print(f"Locator has page attribute: {hasattr(locator, 'page')}")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(test())
