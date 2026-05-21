import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        user_data_dir = '/home/admin1/.config/google-chrome'
        browser_context = await p.chromium.launch_persistent_context(
            user_data_dir,
            channel='chrome',
            headless=True,
            args=['--profile-directory=Default']
        )
        page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
        
        try:
            print("Navigating to Facebook...")
            await page.goto('https://www.facebook.com/', timeout=60000)
            
            search_url = 'https://www.facebook.com/search/posts?q=%E0%B8%8A%E0%B8%B1%E0%B8%8A%E0%B8%8A%E0%B8%B2%E0%B8%95%E0%B8%B4'
            print(f"Navigating to search: {search_url}")
            await page.goto(search_url, timeout=60000)
            
            # Short wait for content
            await page.wait_for_timeout(5000)
            
            final_url = page.url
            title = await page.title()
            email_exists = await page.locator('input[name="email"]').count() > 0
            article_count = await page.locator('[role="article"]').count()
            
            body_text = await page.inner_text('body')
            excerpt = body_text[:200].replace('\n', ' ')
            
            print(f"Final URL: {final_url}")
            print(f"Title: {title}")
            print(f"Email Input Exists: {email_exists}")
            print(f"Article Count: {article_count}")
            print(f"Body Excerpt: {excerpt}")
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser_context.close()

if __name__ == "__main__":
    asyncio.run(run())
