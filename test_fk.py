import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://www.flipkart.com/search?q=asus+laptop+i5&otracker=search")
        await page.wait_for_timeout(3000)
        
        # print all a-tag hrefs
        links = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a')).map(a => `${a.className} | ${a.href}`).filter(h => h.includes('/p/'));
        }''')
        print(len(links), "links found")
        if links:
            print("First few classes for product links:")
            for l in links[:5]: print(l)
            
        await browser.close()

asyncio.run(main())
