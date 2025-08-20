from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import logging
import re
import time
from contextlib import asynccontextmanager

# Scheduler for periodic refresh
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# --- Basic Logging Configuration ---
logging.basicConfig(level=logging.INFO)

# --- Pydantic Models for Data Structure ---
class PrimeRateItem(BaseModel):
    bankName: str
    primeRateValue: str
    bankUpdateDate: str

class HiborItem(BaseModel):
    HIBOR_value: str
    lastUpdateDate: str

class RatesResponse(BaseModel):
    primeRate: List[PrimeRateItem]
    HIBOR: HiborItem

# --- Helper function to format rates ---
def format_rate(rate_str: str) -> str:
    """Converts a rate string to a float, rounds to 2 decimal places, and returns as a string."""
    try:
        rate_float = float(rate_str)
        return f"{rate_float:.2f}"
    except (ValueError, TypeError):
        return rate_str

# --- Main Scraping Logic ---
async def scrape_and_cache_rates():
    """
    This function contains all scraping logic. It's called on startup and periodically.
    """
    logging.info("Starting scheduled scraping process...")
    remote_webdriver_url = "https://standalone-chrome-production-57ca.up.railway.app"
    
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    
    driver = None
    try:
        logging.info(f"Connecting to remote WebDriver at {remote_webdriver_url}...")
        driver = webdriver.Remote(command_executor=remote_webdriver_url, options=chrome_options)
        driver.set_page_load_timeout(30)
        driver.set_window_size(1920, 1080) # Set window size once for all sites
        logging.info("Successfully connected to remote WebDriver.")

        prime_rate_data = [
            {"bankName": "天星銀行", "primeRateValue": "N/A", "bankUpdateDate": "N/A"},
            {"bankName": "華僑永亨", "primeRateValue": "N/A", "bankUpdateDate": "N/A"},
            {"bankName": "工商銀行", "primeRateValue": "N/A", "bankUpdateDate": "N/A"},
        ]
        hibor_data = {"HIBOR_value": "N/A", "lastUpdateDate": "N/A"}
        url_map = {
            "天星銀行": "https://www.airstarbank.com/zh-hk/hkprime",
            "華僑永亨": "https://www.ocbc.com.hk/whb/action/rate/whbRate.do?id=prime_lending_rate&locale=en-us",
            "工商銀行": "https://www.icbcasia.com/hk/en/personal/banking/rate/prime-rate/default.html"
        }
        hibor_url = "https://www.hsbc.com.hk/zh-hk/mortgages/tools/hibor-rate/"

        # --- Scraping Prime Rates ---
        for rate_item in prime_rate_data:
            bank_name = rate_item["bankName"]
            if bank_name not in url_map: continue
            url = url_map[bank_name]
            logging.info(f"Navigating to {url} for {bank_name}...")
            driver.get(url)
            
            if bank_name == "天星銀行":
                try:
                    wait = WebDriverWait(driver, 15)
                    rate_element = wait.until(EC.presence_of_element_located((By.ID, "rate-value")))
                    date_element = wait.until(EC.presence_of_element_located((By.ID, "rate-date")))
                    cleaned_rate = format_rate(rate_element.text.strip('%'))
                    match = re.search(r'(\d{2})/(\d{2})/(\d{4})', date_element.text)
                    formatted_date = f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else "N/A"
                    rate_item.update({"primeRateValue": cleaned_rate, "bankUpdateDate": formatted_date})
                except Exception as e: logging.error(f"Scraping error for {bank_name}: {e}")
            
            elif bank_name == "華僑永亨":
                try:
                    wait = WebDriverWait(driver, 15)
                    date_element = wait.until(EC.presence_of_element_located((By.ID, "UPDATE_prime_lending_rate")))
                    match = re.search(r'(\d{4}-\d{2}-\d{2})', date_element.text)
                    formatted_date = match.group(1) if match else "N/A"
                    rate_xpath = "//table[@bordercolor='#0E5EB8']//tr[last()]/td[last()]/div"
                    rate_element = wait.until(EC.presence_of_element_located((By.XPATH, rate_xpath)))
                    cleaned_rate = format_rate(rate_element.text)
                    rate_item.update({"primeRateValue": cleaned_rate, "bankUpdateDate": formatted_date})
                except Exception as e: logging.error(f"Scraping error for {bank_name}: {e}")

            elif bank_name == "工商銀行":
                try:
                    wait = WebDriverWait(driver, 15)
                    date_id = "asiaHKDInterestRateTimea64b116132c2e18a646eed8bc7102769"
                    wait.until(EC.text_to_be_present_in_element((By.ID, date_id), "/"))
                    date_element = driver.find_element(By.ID, date_id)
                    match = re.search(r'(\d{2})/(\d{2})/(\d{4})', date_element.text)
                    formatted_date = f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else "N/A"
                    tbody_id = "asiaHKDInterestRateTbodya64b116132c2e18a646eed8bc7102769"
                    rate_xpath = f"//tbody[@id='{tbody_id}']//tr[td[1][contains(text(), 'Hong Kong dollar')]]/td[2]"
                    rate_element = wait.until(EC.presence_of_element_located((By.XPATH, rate_xpath)))
                    cleaned_rate = format_rate(rate_element.text)
                    rate_item.update({"primeRateValue": cleaned_rate, "bankUpdateDate": formatted_date})
                except Exception as e: logging.error(f"Scraping error for {bank_name}: {e}")
        
        # --- Scraping HIBOR Rate ---
        logging.info(f"Navigating to {hibor_url} for HIBOR...")
        driver.get(hibor_url)
        try:
            wait = WebDriverWait(driver, 20)
            try:
                phishing_close_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.close-message-button")))
                phishing_close_button.click()
                time.sleep(1)
            except Exception: pass
            
            rate_selector = "div.hibor_rate_table table.desktop tbody tr:first-child td:nth-child(2)"
            wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, rate_selector), "%"))
            
            table_selector = "div.hibor_rate_table table.desktop"
            year_selector = f"{table_selector} thead th:first-child"
            date_selector = f"{table_selector} tbody tr:first-child td:first-child"
            
            year = re.search(r'\d{4}', driver.find_element(By.CSS_SELECTOR, year_selector).text).group(0)
            date_parts = re.findall(r'\d+', driver.find_element(By.CSS_SELECTOR, date_selector).text)
            formatted_date = f"{year}-{int(date_parts[0]):02d}-{int(date_parts[1]):02d}" if len(date_parts) == 2 else "N/A"
            cleaned_rate = format_rate(driver.find_element(By.CSS_SELECTOR, rate_selector).text.strip('%'))
            hibor_data.update({"HIBOR_value": cleaned_rate, "lastUpdateDate": formatted_date})
        except Exception as e: logging.error(f"Scraping error for HIBOR: {e}")

        app.state.rates = {"primeRate": prime_rate_data, "HIBOR": hibor_data}
        logging.info(f"Scraping process finished. Cached data: {app.state.rates}")

    except Exception as e:
        logging.error(f"A top-level error occurred during web scraping: {e}")
    finally:
        if driver:
            logging.info("Closing WebDriver session.")
            driver.quit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Application starting up...")
    app.state.rates = {}
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scrape_and_cache_rates, 'interval', hours=4)
    scheduler.start()
    await scrape_and_cache_rates()
    yield
    logging.info("Application shutting down...")
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

# origins = [
#     "http://localhost",
#     "http://localhost:3000",
#     "http://localhost:5173", # Add other frontend ports if needed
# ]
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/get-rates", response_model=RatesResponse)
async def get_rates():
    """Instantly returns the cached bank rate data."""
    if not app.state.rates:
        return {"primeRate": [{"bankName": "天星銀行", "primeRateValue": "Loading...", "bankUpdateDate": "Loading..."}, {"bankName": "華僑永亨", "primeRateValue": "Loading...", "bankUpdateDate": "Loading..."}, {"bankName": "工商銀行", "primeRateValue": "Loading...", "bankUpdateDate": "Loading..."}], "HIBOR": {"HIBOR_value": "Loading...", "lastUpdateDate": "Loading..."}}
    return app.state.rates