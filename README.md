# IT Procurement Agent

An autonomous prototype that searches Amazon.in and Flipkart, verifies product specifications using LLM, and returns strictly matching results for hardware procurement requests.

## 🚀 Key Features

- **Concurrent Scraping**: Searches Amazon and Flipkart simultaneously using Playwright.
- **Spec Normalization**: Standardizes raw spec strings (e.g., "16 GB DDR4" → "16GB") for reliable comparison.
- **Strict AI Evaluation**: Uses Groq (Llama 3.3 70B) with a "Reject-by-Default" logic to verify hardware constraints.
- **Modern UI**: Clean, responsive dashboard with progress tracking and stat summaries.
- **Anti-Bot Stealth**: Custom browser contexts and request pacing to avoid detection.

## 🛠️ Architecture

The system follows a 7-stage pipeline:
1. **Requirement Parsing**: Regex-based extraction of category, brands, CPU, RAM, etc.
2. **Query Building**: Generates specific search terms + fallbacks.
3. **Concurrent Scraping**: Playwright handles JS-heavy pages on both platforms.
4. **Spec Normalisation**: Raw data is cleaned and standardised.
5. **LLM Evaluation**: Groq (Llama 3.3) strictly validates normalised specs against requirements.
6. **Result Formatting**: Products are categorised into Approved or Rejected.
7. **UI Delivery**: FastAPI serves results to the web dashboard.

## 📦 Tech Stack

- **Backend**: Python, FastAPI, Uvicorn
- **Automation**: Playwright (Chromium)
- **AI**: Groq Llama 3.3 70B (groq SDK)
- **Frontend**: Vanilla JS, HTML5, CSS3, Bootstrap 5
- **Config**: python-dotenv

## ⚙️ Setup & Installation

### 1. Prerequisites
- Python 3.11+
- Google Gemini API Key

### 2. Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium --with-deps
```

### 3. Environment Config
Create a `.env` file from the example:
```bash
cp .env.example .env
```
Edit `.env` and add your `GEMINI_API_KEY`.

### 4. Run the App
```bash
python main.py
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

## 📝 Design Notes

**Architecture & Implementation**:
The system follows a 7-stage pipeline: requirement parsing → query building → concurrent scraping → spec normalisation → LLM evaluation → result formatting → UI display. Amazon and Flipkart are scraped simultaneously using `asyncio.gather()` in two separate Playwright browser contexts, reducing total search time by approximately 50% compared to sequential execution. Playwright was chosen as the sole scraping tool because both Amazon and Flipkart are JavaScript-heavy applications that cannot be scraped with requests-based tools.

**Tool Choices & Performance**:
Playwright handles JavaScript rendering, anti-bot stealth setup, and DOM interaction in a single library — replacing the requests + BeautifulSoup + Selenium stack entirely. The `navigator.webdriver` property is overridden via an init script to prevent bot detection. Gemini 1.5 Flash was chosen as the LLM evaluator because it is free, fast, and reliably produces structured JSON output. Temperature is set to 0.0 to ensure deterministic evaluation — the same product always receives the same verdict. The LLM prompt enforces REJECT-by-default with explicit rules covering every edge case including missing specs, ambiguous specs, and partial matches.

**Reliability & Future Improvements**:
Sponsored listings are detected using three independent methods per platform — dedicated element selectors, aria-label attributes, and line-by-line text scanning — because any single method alone produces false positives. Spec normalisation runs before LLM evaluation to eliminate string comparison issues such as "8 GB DDR4" vs "8GB" appearing as different values. CAPTCHA detection aborts the affected platform gracefully without crashing the pipeline. Given more time, improvements would include: Redis-based job storage for production scale, CAPTCHA-solving via 2captcha or anti-captcha services, result caching to avoid re-scraping identical queries, support for additional platforms like Croma and Reliance Digital, and email notifications when a procurement search completes.

---
*Created for the Autonomous IT Procurement Agent Assignment.*
