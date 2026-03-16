import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://www.flipkart.com/search?q=asus+laptop+i5&otracker=search")
        await page.wait_for_timeout(3000)
        
        # print the inner HTML of the first product a-tag's parent container
        html = await page.evaluate('''() => {
            const a = document.querySelector('a[href*="/p/"]');
            return a ? a.parentElement.parentElement.outerHTML : "No link found";
        }''')
        print(html[:1500])
        await browser.close()

asyncio.run(main())
