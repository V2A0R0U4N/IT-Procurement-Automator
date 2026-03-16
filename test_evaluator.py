import asyncio
from core.evaluator import LLMEvaluator

async def main():
    ev = LLMEvaluator()
    req = { "Storage": "512GB SSD", "Processor": "M4", "Brand": "Apple" }
    prod = {
        "title": "Apple 2025 MacBook Air (13-inch, Apple M4 chip with 10-core CPU and 8-core GPU, 16GB Unified Memory, 256GB) - Midnight",
        "price_raw": "₹88,990",
        "platform": "Amazon",
        "specs": {
            "Brand": "Apple",
            "Processor Brand": "Apple",
            "Processor Type": "Apple M4",
            "Memory Technology": "Unified Memory",
            "Hard Drive Size": "256 GB"
        }
    }
    
    prod_missing = {
        "title": "Apple MacBook Air M4",
        "price_raw": "₹56,990",
        "platform": "Flipkart",
        "specs": {
            "Brand": "Apple",
            "Processor Brand": "Apple",
            "Processor Type": "Apple M4",
            "Memory Technology": "United Memory"
        }
    }
    
    print("Test 1 (256GB vs 512GB):")
    res1 = await ev.evaluate(req, prod)
    print(res1)
    
    print("\nTest 2 (Missing entirely vs 512GB):")
    res2 = await ev.evaluate(req, prod_missing)
    print(res2)

asyncio.run(main())
