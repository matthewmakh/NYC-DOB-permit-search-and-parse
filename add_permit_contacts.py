from dotenv import load_dotenv
load_dotenv()
import os
import subprocess
import shutil

'''import os
os.environ['UC_CHROMEDRIVER_VERSION'] = '136' '''
import time
import random
from bs4 import BeautifulSoup
import mysql.connector
import psycopg2
import psycopg2.extras
from datetime import datetime
from fake_useragent import UserAgent
import requests

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

#from seleniumwire import undetected_chromedriver as uc  # Use selenium-wire wrapper
import undetected_chromedriver as uc  # ✅ Correct import

from remote_add_permit_contacts import remote_scraper

# Database configuration
DB_TYPE = os.getenv('DB_TYPE', 'postgresql')  # Default to PostgreSQL (Railway)

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

# -------------------- CONFIG --------------------

USE_PROXY = False
DECODE_PROXY_PORTS = [10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008, 10009]
proxy_index = 0

PROXY_HOST = os.getenv('PROXY_HOST')
PROXY_USER = os.getenv('PROXY_USER')
PROXY_PASS = os.getenv('PROXY_PASS')

USER_AGENT = UserAgent().chrome

# -------------------- PRINT CURRENT IP --------------------

try:
    my_ip = requests.get("https://ipinfo.io/json", timeout=10).json().get("ip")
    print(f"🔍 Current Machine IP: {my_ip}")
except Exception as e:
    print(f"⚠️ Could not retrieve local IP: {e}")

# -------------------- DATABASE --------------------

if DB_TYPE == 'postgresql':
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', '5432')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
else:  # mysql
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME')
    )
    cursor = conn.cursor()

cursor.execute("SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1")
config = cursor.fetchone()

if DB_TYPE == 'postgresql':
    start_month = config['start_month']
    start_day = config['start_day']
    start_year = config['start_year']
    permit_type = config['permit_type']
    contact_search_limit = config['max_successful_links']
else:
    start_month, start_day, start_year, permit_type, contact_search_limit = config[1], config[2], config[3], config[4], config[9]

print(f'latest config: {config}')

rate_limit_count = 0
MAX_RATE_LIMITS = 1  # You can adjust this

# Use the contact_search_limit from the database config
MAX_SUCCESSFUL_LINKS = contact_search_limit
successful_links_opened = 0
proxy_rotation_count = 0
MAX_PROXY_ROTATIONS = 1
print(f"Searching For {MAX_SUCCESSFUL_LINKS} Contacts (from database config)")

# -------------------- UTILS --------------------

def human_delay(min_sec=2.0, max_sec=20.0):
    x = random.uniform(min_sec, max_sec)
    print(f'Sleeping for {x} Seconds to Simulate Human Behavior')
    time.sleep(x)

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

# -------------------- PROXY HEALTH TEST --------------------

def test_proxy_health():
    global proxy_index
    proxy_port = DECODE_PROXY_PORTS[proxy_index % len(DECODE_PROXY_PORTS)]
    proxies = {
        "http": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{proxy_port}",
        "https": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{proxy_port}"
    }

    print("🧪 Testing proxy health...")
    try:
        response = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=10)
        data = response.json()
        actual_ip = data.get("ip")
        city = data.get("city", "Unknown City")
        region = data.get("region", "Unknown Region")
        country = data.get("country", "Unknown Country")
        timezone = data.get("timezone", "Unknown Timezone")

        print(f"✅ Public IP (Proxy): {actual_ip}")
        print(f"🌍 Location: {city}, {region}, {country}")
        print(f"🕒 Timezone: {timezone}")

        if not actual_ip:
            print("❌ No IP detected. Proxy might be blocked or down.")
    except Exception as e:
        print(f"❌ Proxy test failed: {e}")



# -------------------- SELENIUM DRIVER --------------------

def create_driver():
    global proxy_index
    proxy_port = random.choice(DECODE_PROXY_PORTS)
    proxy_index += 1

    # --- Setup Chrome Options ---
    options = uc.ChromeOptions()
    
    # Set Chrome binary if found
    if CHROME_BINARY_PATH:
        options.binary_location = CHROME_BINARY_PATH

    # ❌ Commented out headless mode - running in visible mode for debugging
    # options.add_argument("--headless=new")  # 🧠 Use "new" for latest Chromium headless mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # options.add_argument("--disable-gpu")  # Not needed in non-headless
    # options.add_argument("--disable-software-rasterizer")  # Not needed in non-headless

    # ✅ Keep - For SSL & certificate flexibility (safe for scraping)
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")

    # ✅ Keep - Non-critical, for compatibility
    options.add_argument("--lang=en-US,en;q=0.9")
    options.add_argument(f"--user-agent={UserAgent().chrome}")
    
    # 🖥️ Window settings for visible mode
    options.add_argument("--start-maximized")

    # ✅ Optional - Keep if using a Chrome profile (only works non-headless usually)
    options.add_argument("--profile-directory=Default")

    # ✅ Configure Proxy (only if USE_PROXY is True)
    if USE_PROXY:
        proxy_auth = f"{PROXY_USER}:{PROXY_PASS}"
        proxy_string = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{proxy_port}"
        options.add_argument(f'--proxy-server=http://{PROXY_HOST}:{proxy_port}')
        print(f"🔄 Using Secure Auth Proxy: {PROXY_HOST}:{proxy_port}")
    else:
        print("🌐 Running without proxy (direct connection)")
        proxy_string = None

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


    # --- Inject Basic Proxy Headers (if needed) ---
    def interceptor(request):
        if "WorkPermitDataServlet" in request.url:
            request.headers["Referer"] = "https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp"
            request.headers["Origin"] = "https://a810-bisweb.nyc.gov"
            request.headers["Accept-Language"] = "en-US,en;q=0.9"

    driver.request_interceptor = interceptor

    # --- Optional: Spoof timezone (only if using proxy) ---
    if USE_PROXY and proxy_string:
        try:
            proxies = {
                "http": proxy_string,
                "https": proxy_string,
            }
            ip_info = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=10).json()
            detected_timezone = ip_info.get("timezone")
            if detected_timezone:
                driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {
                    "timezoneId": detected_timezone
                })
                print(f"🕒 Spoofed timezone to match proxy: {detected_timezone}")
        except Exception as e:
            print(f"⚠️ Timezone spoofing failed: {e}")

    return driver


# -------------------- SCRAPING FUNCTIONS --------------------

def is_access_denied(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        return "Access Denied" in body_text or "You don't have permission" in body_text
    except:
        return False

def extract_names_and_phones(driver):
    try:
        table = driver.find_element(By.XPATH, "/html/body/center/table[7]")
        html = table.get_attribute("outerHTML")
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")

        people = []
        current_name = None

        for row in rows:
            text = row.get_text(separator=" ", strip=True)
            if "Issued to:" in text:
                current_name = text.replace("Issued to:", "").strip()
            elif "Superintendent of Construction:" in text:
                current_name = text.replace("Superintendent of Construction:", "").strip()
            elif "Site Safety Manager:" in text:
                current_name = text.replace("Site Safety Manager:", "").strip()
            elif "Business:" in text and text.replace("Business:", "").strip():
                current_name = text.replace("Business:", "").strip()
            if "Phone:" in text:
                phone = text.split("Phone:")[-1].strip()
                if current_name:
                    people.append((current_name, phone))
                    current_name = None

        def get_text_by_label(label):
            try:
                label_cell = driver.find_element(By.XPATH, f"//td[contains(text(), '{label}')]")
                value_cell = label_cell.find_element(By.XPATH, "./following-sibling::td[1]")
                return value_cell.text.strip()
            except:
                return None

        details = {
            "use": get_text_by_label("Use:"),
            "stories": get_text_by_label("Stories:"),
            "total_units": get_text_by_label("Total Number of Dwelling Units at Location:"),
            "occupied_units": get_text_by_label("Number of Dwelling Units Occupied During Construction:")
        }

        return people, details
    except Exception as e:
        print(f"⚠️ Failed to extract data: {e}")
        return [], {"use": None, "stories": None, "total_units": None, "occupied_units": None}

def process_permit_page(driver):
    global rate_limit_count, successful_links_opened
    permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
    print(f"✅ Found {len(permit_links)} permit links.")

    for i in range(len(permit_links)):
        if successful_links_opened >= MAX_SUCCESSFUL_LINKS:
            print(f"✅ Limit of {MAX_SUCCESSFUL_LINKS} reached.")
            return

        if rate_limit_count >= MAX_RATE_LIMITS:
            print(f"🚫 Rate limit threshold reached during loop ({rate_limit_count}). Stopping page processing.")
            return

        permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
        if i >= len(permit_links): break

        link_element = permit_links[i]
        permit_no = link_element.text.strip()

        cursor.execute("SELECT id FROM permits WHERE permit_no = %s", (permit_no,))
        result = cursor.fetchone()
        if not result:
            print(f"❌ Skipping untracked permit: {permit_no}")
            continue

        permit_id = result[0]
        cursor.execute("SELECT 1 FROM contacts WHERE permit_id = %s AND is_checked = TRUE LIMIT 1", (permit_id,))
        if cursor.fetchone():
            print(f"✅ Already checked: {permit_no}")
            continue

        try:
            print(f"➡️ Clicking permit {permit_no}...")
            driver.execute_script("arguments[0].setAttribute('target','_self')", link_element)
            link_element.click()
            human_delay()

            if is_access_denied(driver):
                print("🚫 Access Denied. Skipping this permit.")
                rate_limit_count += 1
                driver.back()
                human_delay()
                continue

            contacts, permit_info = extract_names_and_phones(driver)
            print(f"🔍 Permit Details: {permit_info}")

            if not contacts:
                cursor.execute("INSERT INTO contacts (permit_id, is_checked) VALUES (%s, %s)", (permit_id, True))
                conn.commit()
                successful_links_opened += 1
                driver.back()
                human_delay()
                continue

            for name, phone in contacts:
                cursor.execute("""
                    INSERT INTO contacts (permit_id, name, phone, is_checked)
                    VALUES (%s, %s, %s, %s)
                """, (permit_id, name, phone, True))
            conn.commit()

            cursor.execute("""
                UPDATE permits
                SET use_type = %s,
                    stories = %s,
                    total_units = %s,
                    occupied_units = %s
                WHERE id = %s
            """, (
                permit_info['use'],
                permit_info['stories'],
                permit_info['total_units'],
                permit_info['occupied_units'],
                permit_id
            ))
            conn.commit()

            successful_links_opened += 1

        except Exception as e:
            print(f"⚠️ Error on permit {permit_no}: {e}")
            rate_limit_count += 1

        print("🔙 Returning to results page...")
        driver.back()
        human_delay()


def go_to_next_page(driver):
    try:
        next_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/center/table[4]/tbody/tr/td[3]/form/input[1]'))
        )
        next_button.click()
        human_delay()
        return True
    except:
        return False

# -------------------- MAIN --------------------

def run_scraper():
    global rate_limit_count, successful_links_opened

    try:
        test_proxy_health()
        driver = create_driver()
        wait = WebDriverWait(driver, 10)

        print("🟡 Opening search form...")
        driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
        time.sleep(random.uniform(0.35, 4.65))

        wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
        form_inputs = driver.find_element(By.ID, 'allstartdate_month')
        driver.execute_script("arguments[0].scrollIntoView({ behavior: 'smooth', block: 'center' });", form_inputs)

        Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value(f"{int(start_month):02}")
        time.sleep(random.uniform(0.35, 2.65))

        day_str = f"{int(start_day):02}"
        input_field = driver.find_element(By.ID, 'allstartdate_day')
        for char in day_str:
            input_field.send_keys(char)
            time.sleep(random.uniform(0.08, 0.25))

        driver.find_element(By.ID, 'allstartdate_year').send_keys(start_year)

        time.sleep(random.uniform(0.35, 3.14))
        Select(driver.find_element(By.ID, 'allpermittype')).select_by_value(permit_type)
        time.sleep(random.uniform(0.35, 3.14))
        driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
        human_delay()

        while True:
            if successful_links_opened >= MAX_SUCCESSFUL_LINKS:
                print(f"✅ Limit of {MAX_SUCCESSFUL_LINKS} reached. Stopping main loop.")
                return "success"

            if rate_limit_count >= MAX_RATE_LIMITS:
                print(f"🚫 Rate limit threshold reached ({rate_limit_count}). Exiting early.")
                return "rate_limited"

            process_permit_page(driver)
            human_delay()

            if not go_to_next_page(driver):
                print("🖚 No more pages.")
                return "success"

    except Exception as e:
        print(f"❌ Script failed: {e}")
        return "error"

    finally:
        print(f"🖁 Done. Total rate limit evasions: {rate_limit_count}")
        try:
            driver.quit()
            del driver
        except Exception as e:
            print(f"⚠️ Cleanup issue: {e}")
        cursor.close()
        conn.close()

# -------------------- ENTRY POINT --------------------
if __name__ == "__main__":
    result = run_scraper()

    if result == "rate_limited":
        print("🧐 Triggering backup scraper due to rate limit...")
        remaining = MAX_SUCCESSFUL_LINKS - successful_links_opened
        remote_scraper(remaining)
    elif result == "error":
        print("⚠️ Script ended with an error. Investigate.")
    else:
        print("✅ Scraper finished successfully.")
