import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://www.flipkart.com/asus-vivobook-15-backlit-keyboard-intel-core-i5-12th-gen-1235u-8-gb-512-gb-ssd-windows-11-home-x1504za-nj521ws-x1504za-nj520ws-x1504za-nj528ws-thin-light-laptop/p/itmff0e170cb596e?pid=COMHYWSWMFQQBCG3")
        await page.wait_for_timeout(3000)
        
        html = await page.content()
        with open("fk_prod.html", "w") as f:
            f.write(html)
        print("saved to fk_prod.html")
        await browser.close()

asyncio.run(main())
