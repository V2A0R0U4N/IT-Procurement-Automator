import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://www.flipkart.com/asus-vivobook-15-backlit-keyboard-intel-core-i5-12th-gen-1235u-8-gb-512-gb-ssd-windows-11-home-x1504za-nj521ws-x1504za-nj520ws-x1504za-nj528ws-thin-light-laptop/p/itmff0e170cb596e?pid=COMHYWSWMFQQBCG3")
        await page.wait_for_timeout(3000)
        
        # Check title
        title = await page.evaluate('''() => {
            const el = document.querySelector('.VU-ZEz, span.B_NuCI');
            return el ? el.innerText : "Not found";
        }''')
        
        # Check price
        price = await page.evaluate('''() => {
            const el = document.querySelector('.Nx9bqj.CxhGGd, ._30jeq3._16Jk6d');
            return el ? el.innerText : "Not found";
        }''')
        
        # Check specs
        specs = await page.evaluate('''() => {
            let trs = document.querySelectorAll('tr.row');
            return trs.length > 0 ? trs.length : document.querySelectorAll('td').length;
        }''')
        
        print(f"Title: {title}")
        print(f"Price: {price}")
        print(f"Specs table rows: {specs}")
        await browser.close()

asyncio.run(main())
