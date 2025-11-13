from dotenv import load_dotenv
load_dotenv()
import os
import subprocess
import shutil
import time
import random
from bs4 import BeautifulSoup
import mysql.connector
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime

# -------------------- DYNAMIC PATH DETECTION --------------------

def find_chromedriver():
    """Dynamically find ChromeDriver across different systems"""
    possible_paths = [
        '/opt/homebrew/bin/chromedriver',  # macOS Apple Silicon (M1/M2)
        '/usr/local/bin/chromedriver',     # macOS Intel / Linux brew
        '/usr/bin/chromedriver',           # Linux system install
        shutil.which('chromedriver'),      # Search in PATH
    ]
    
    for path in possible_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            print(f"✅ Found ChromeDriver at: {path}")
            return path
    
    # Don't raise error - let undetected-chromedriver handle it
    print("⚠️ ChromeDriver not found in standard locations. Letting undetected-chromedriver auto-download.")
    return None

def find_chrome_binary():
    """Dynamically find Chrome/Chromium binary across different systems"""
    possible_paths = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',  # macOS Chrome
        '/Applications/Chromium.app/Contents/MacOS/Chromium',            # macOS Chromium
        '/usr/bin/google-chrome',          # Linux Chrome
        '/usr/bin/chromium-browser',       # Linux Chromium
        '/usr/bin/chromium',               # Alternative Linux Chromium
        shutil.which('google-chrome'),     # Search in PATH
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
    ]
    
    for path in possible_paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            print(f"✅ Found Chrome/Chromium at: {path}")
            return path
    
    # Fallback: let undetected-chromedriver use its default
    print("⚠️ Chrome binary not found in standard locations. Using undetected-chromedriver default.")
    return None

def get_chrome_version(chrome_path):
    """Get the installed Chrome version"""
    if not chrome_path:
        return None
    
    try:
        # Try to get version from Chrome binary
        result = subprocess.run(
            [chrome_path, '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        version_str = result.stdout.strip()
        # Extract major version number (e.g., "Google Chrome 142.0.7444.135" -> 142)
        import re
        match = re.search(r'(\d+)\.\d+\.\d+\.\d+', version_str)
        if match:
            version = int(match.group(1))
            print(f"🔍 Detected Chrome version: {version}")
            return version
    except Exception as e:
        print(f"⚠️ Could not detect Chrome version: {e}")
    
    return None

# Detect paths at startup
CHROMEDRIVER_PATH = find_chromedriver()
CHROME_BINARY_PATH = find_chrome_binary()
CHROME_VERSION = get_chrome_version(CHROME_BINARY_PATH)

# Configure environment for undetected-chromedriver only if we found chromedriver
if CHROMEDRIVER_PATH:
    os.environ['UC_SKIP_DOWNLOAD'] = 'true'
    os.environ['UC_CHROMEDRIVER_BINARY'] = CHROMEDRIVER_PATH
    os.environ['UC_DISABLE_AUTO_PATCHER'] = '1'
else:
    # Let undetected-chromedriver download and manage ChromeDriver
    print("🔧 Allowing undetected-chromedriver to auto-manage ChromeDriver")

# Set Chrome version dynamically
if CHROME_VERSION:
    os.environ['UC_CHROMEDRIVER_VERSION'] = str(CHROME_VERSION)
    print(f"🎯 Using Chrome version: {CHROME_VERSION}")
else:
    # Fallback to a recent version
    os.environ['UC_CHROMEDRIVER_VERSION'] = '142'
    print("⚠️ Chrome version not detected, using default: 142")

os.environ['UC_KEEP_USER_DATA_DIR'] = '1'

# -------------------- UTILITY FUNCTIONS --------------------

def human_delay(min_sec=2.0, max_sec=4.0):
    time.sleep(random.uniform(min_sec, max_sec))

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

# -------------------- MAIN SCRIPT --------------------

def main():
    # DB Connection
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'scraper_user'),
        password=os.getenv('DB_PASSWORD', 'Tyemakharadze9'),
        database=os.getenv('DB_NAME', 'permit_scraper')
    )
    cursor = conn.cursor()

    #get permit search info from database
    cursor.execute("""
    SELECT * FROM permit_search_config
    ORDER BY created_at DESC
    LIMIT 1;
    """)

    config = cursor.fetchone()

    start_month = config[1]
    start_day = config[2]
    start_year = config[3]
    permit_type = config[4]

    print(f'latest config: {config}')

    # ✅ Check for existing contact_scrape_jobs row or create one
    cursor.execute("""
        SELECT id FROM contact_scrape_jobs
        WHERE permit_type = %s AND start_month = %s AND start_day = %s AND start_year = %s
        ORDER BY created_at DESC LIMIT 1
    """, (permit_type, start_month, start_day, start_year))
    result = cursor.fetchone()
    if result:
        job_id = result[0]
        print(f"[INFO] Existing contact scrape job found: ID {job_id}")
    else:
        cursor.execute("""
            INSERT INTO contact_scrape_jobs (permit_type, start_month, start_day, start_year)
            VALUES (%s, %s, %s, %s)
        """, (permit_type, start_month, start_day, start_year))
        conn.commit()
        cursor.execute("SELECT LAST_INSERT_ID()")
        job_id = cursor.fetchone()[0]
        print(f"[INFO] New contact scrape job created: ID {job_id}")

    # ✅ Track count of permits before insertion
    cursor.execute("SELECT COUNT(*) FROM permits")
    before_count = cursor.fetchone()[0]

    # Define nested helper functions
    def extract_permits(driver):
        soup = BeautifulSoup(driver.page_source, "html.parser")
        rows = soup.select("body > center > table:nth-of-type(3) > tbody > tr")
        data = []

        for row in rows:
            cols = row.find_all("td")
            if len(cols) != 7:
                continue

            if "APPLICANT" in cols[0].get_text().upper():
                continue

            permit_link = cols[1].find("a")
            link = "https://a810-bisweb.nyc.gov/bisweb/" + permit_link['href'] if permit_link else ""

            values = [col.get_text(strip=True).replace('\xa0', ' ') for col in cols]
            values.append(link)
            data.append(values)

        return data

    def insert_permits(data):
        inserted = 0
        for row in data:
            try:
                applicant, permit_no, job_type, issue_date, exp_date, bin_no, address, link = row

                cursor.execute("SELECT 1 FROM permits WHERE permit_no = %s", (permit_no,))
                if cursor.fetchone():
                    continue

                cursor.execute("""
                    INSERT INTO permits (
                        job_id, applicant, permit_no, job_type, issue_date, exp_date, bin, address, link
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    job_id, applicant, permit_no, job_type,
                    fix_date_format(issue_date),
                    fix_date_format(exp_date),
                    bin_no, address, link
                ))
                inserted += 1
            except Exception as e:
                print("❌ Error inserting permit:", e)

        conn.commit()
        print(f"✅ Actually inserted {inserted} permits.")

    def go_to_next(driver):
        try:
            next_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, '/html/body/center/table[4]/tbody/tr/td[3]/form/input[1]'))
            )
            next_btn.click()
            human_delay()
            return True
        except:
            return False

    # Start the scraper

def create_driver():
    options = uc.ChromeOptions()

    # Set Chrome binary if found
    if CHROME_BINARY_PATH:
        options.binary_location = CHROME_BINARY_PATH

    # ❌ Commented out headless mode - running in visible mode for debugging
    # options.add_argument("--headless=new")
    # options.add_argument("--disable-gpu")
    # options.add_argument("--disable-software-rasterizer")
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # ✅ Keep - Helps bypass bot detection
    options.add_argument("--disable-blink-features=AutomationControlled")

    # ✅ Keep - Custom UA is good for stealth
    options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36")

    # 🖥️ Window settings for visible mode
    options.add_argument("--start-maximized")

    # --- Launch UC Chrome with dynamic paths ---
    driver_kwargs = {
        'options': options,
        'version_main': CHROME_VERSION if CHROME_VERSION else 142,  # Use detected version or fallback
        'use_subprocess': False
    }
    
    # Only set driver_executable_path if we found it
    if CHROMEDRIVER_PATH:
        driver_kwargs['driver_executable_path'] = CHROMEDRIVER_PATH
    
    # Only set browser_executable_path if we found it
    if CHROME_BINARY_PATH:
        driver_kwargs['browser_executable_path'] = CHROME_BINARY_PATH
    
    driver = uc.Chrome(**driver_kwargs)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    })
    return driver

if __name__ == '__main__':
    main()
