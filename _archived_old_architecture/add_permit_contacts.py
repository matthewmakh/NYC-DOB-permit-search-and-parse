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

from seleniumwire import undetected_chromedriver as uc  # Use selenium-wire wrapper for proxy auth
#import undetected_chromedriver as uc  # ✅ Standard import (no proxy auth support)

# Remote scraper fallback for rate limits
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

USE_PROXY = False  # Set to True when ready to use proxies
DECODE_PROXY_PORTS = [10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008, 10009]

# Pick one random proxy at start of session and use it for entire run
PROXY_PORT = random.choice(DECODE_PROXY_PORTS)
print(f"🔀 Selected proxy port for this session: {PROXY_PORT}")

PROXY_HOST = os.getenv('PROXY_HOST', 'gate.decodo.com')
PROXY_USER = os.getenv('PROXY_USER', 'spckyt8xpj')
PROXY_PASS = os.getenv('PROXY_PASS', 'r~P6RwgDe6hjh6jb6W')

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
MAX_RATE_LIMITS = 3  # Switch to remote scraper after 3 rate limit hits

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
    proxies = {
        "http": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}",
        "https": f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
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

    # ✅ Configure Proxy (using the port selected at session start)
    if USE_PROXY:
        proxy_string = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
        print(f"🔄 Using Proxy: {PROXY_HOST}:{PROXY_PORT}")
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
    
    # ✅ IMPORTANT: seleniumwire_options for proxy authentication
    if USE_PROXY and proxy_string:
        driver_kwargs['seleniumwire_options'] = {
            'proxy': {
                'http': proxy_string,
                'https': proxy_string,
                'no_proxy': 'localhost,127.0.0.1'
            }
        }
    
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
        
        def extract_bbl_info():
            """Extract Block and Lot from table[2] (Field 1)"""
            try:
                # BBL info is in table[2]: "BIN: 3428710    Block: 5008    Lot: 65"
                table2 = driver.find_element(By.XPATH, "/html/body/center/table[2]")
                table2_text = table2.text
                
                import re
                block_match = re.search(r'Block:\s*(\d+)', table2_text)
                lot_match = re.search(r'Lot:\s*(\d+)', table2_text)
                
                block = block_match.group(1) if block_match else None
                lot = lot_match.group(1) if lot_match else None
                
                if not block or not lot:
                    print(f"⚠️ BBL extraction incomplete - Block: {block}, Lot: {lot}")
                    print(f"   Table content: {table2_text[:200]}")
                
                return {
                    'block': block,
                    'lot': lot
                }
            except Exception as e:
                print(f"❌ BBL extraction failed: {e}")
                try:
                    print(f"   Page source available: {len(driver.page_source)} bytes")
                except:
                    pass
                return {'block': None, 'lot': None}
        
        def extract_permit_details():
            """Extract specific permit details"""
            try:
                details = {}
                
                # Field 14: Job Number - from "Job No:" field
                try:
                    job_no_elem = driver.find_element(By.XPATH, "//td[contains(text(), 'Job No:')]/following-sibling::td[1]")
                    details['job_number'] = job_no_elem.text.strip()
                except:
                    details['job_number'] = None
                
                # Field 8: Filing Date - might have "ERENEWAL" or other suffix
                filing_text = get_text_by_label("Filing Date:")
                if filing_text:
                    # Extract just the date part (MM/DD/YYYY)
                    import re
                    date_match = re.search(r'(\d{2}/\d{2}/\d{4})', filing_text)
                    details['filing_date'] = date_match.group(1) if date_match else None
                else:
                    details['filing_date'] = None
                
                # Field 10: Status
                details['status'] = get_text_by_label("Status:")
                
                # Field 7: Fee Type
                details['fee_type'] = get_text_by_label("Fee:")
                
                # Field 11: Proposed Job Start
                details['proposed_job_start'] = get_text_by_label("Proposed Job Start:")
                
                # Field 12: Work Approved
                details['work_approved'] = get_text_by_label("Work Approved:")
                
                # Field 3: Site Fill
                details['site_fill'] = get_text_by_label("Site Fill:")
                
                # Field 13: Work Description - combine all work description rows
                try:
                    work_desc_parts = []
                    # Look for the "Work:" label and collect subsequent content rows
                    work_label = driver.find_element(By.XPATH, "//td[@class='label' and contains(text(), 'Work:')]")
                    # Get parent row and following siblings
                    parent_row = work_label.find_element(By.XPATH, "./..")
                    following_rows = parent_row.find_elements(By.XPATH, "./following-sibling::tr")
                    
                    for row in following_rows[:5]:  # Limit to next 5 rows to avoid going too far
                        cells = row.find_elements(By.TAG_NAME, "td")
                        for cell in cells:
                            if cell.get_attribute("class") == "content":
                                text = cell.text.strip()
                                if text and not text.startswith("--") and len(text) > 5:
                                    work_desc_parts.append(text)
                        # Stop if we hit a separator or new section
                        if any(cell.get_attribute("class") == "label" for cell in cells):
                            break
                    
                    details['work_description'] = ' '.join(work_desc_parts) if work_desc_parts else None
                except:
                    details['work_description'] = None
                
                # Field 5: Total Dwelling Units
                total_units_text = get_text_by_label("Total Number of Dwelling Units at Location:")
                details['total_dwelling_units'] = int(total_units_text) if total_units_text and total_units_text.isdigit() else None
                
                # Field 6: Dwelling Units Occupied
                occupied_text = get_text_by_label("Number of Dwelling Units Occupied During Construction:")
                details['dwelling_units_occupied'] = int(occupied_text) if occupied_text and occupied_text.isdigit() else None
                
                return details
            except Exception as e:
                print(f"⚠️ Error extracting permit details: {e}")
                return {}

        # Get all the information
        bbl_info = extract_bbl_info()
        permit_details = extract_permit_details()
        
        details = {
            "use": get_text_by_label("Use:"),
            "stories": get_text_by_label("Stories:"),
            "total_units": get_text_by_label("Total Number of Dwelling Units at Location:"),
            "occupied_units": get_text_by_label("Number of Dwelling Units Occupied During Construction:"),
            # Field 1: BBL info
            "block": bbl_info['block'],
            "lot": bbl_info['lot'],
            # Field 3: Site Fill
            "site_fill": permit_details.get('site_fill'),
            # Fields 5 & 6: Dwelling units
            "total_dwelling_units": permit_details.get('total_dwelling_units'),
            "dwelling_units_occupied": permit_details.get('dwelling_units_occupied'),
            # Field 7: Fee Type
            "fee_type": permit_details.get('fee_type'),
            # Field 8: Filing Date
            "filing_date": permit_details.get('filing_date'),
            # Field 10: Status
            "status": permit_details.get('status'),
            # Fields 11 & 12: Dates
            "proposed_job_start": permit_details.get('proposed_job_start'),
            "work_approved": permit_details.get('work_approved'),
            # Field 13: Work Description
            "work_description": permit_details.get('work_description'),
            # Field 14: Job Number
            "job_number": permit_details.get('job_number')
        }

        return people, details
    except Exception as e:
        print(f"⚠️ Failed to extract data: {e}")
        return [], {
            "use": None, "stories": None, "total_units": None, "occupied_units": None,
            "block": None, "lot": None, "site_fill": None, "total_dwelling_units": None,
            "dwelling_units_occupied": None, "fee_type": None, "filing_date": None,
            "status": None, "proposed_job_start": None, "work_approved": None,
            "work_description": None, "job_number": None
        }

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

        permit_id = result['id'] if isinstance(result, dict) else result[0]
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

            # Helper function to convert date strings to proper format
            def convert_date(date_str):
                if not date_str:
                    return None
                try:
                    from datetime import datetime
                    return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
                except:
                    return None

            cursor.execute("""
                UPDATE permits
                SET use_type = %s,
                    stories = %s,
                    total_units = %s,
                    occupied_units = %s,
                    block = %s,
                    lot = %s,
                    site_fill = %s,
                    total_dwelling_units = %s,
                    dwelling_units_occupied = %s,
                    fee_type = %s,
                    filing_date = %s,
                    status = %s,
                    proposed_job_start = %s,
                    work_approved = %s,
                    work_description = %s,
                    job_number = %s
                WHERE id = %s
            """, (
                permit_info['use'],
                permit_info['stories'],
                permit_info['total_units'],
                permit_info['occupied_units'],
                permit_info['block'],
                permit_info['lot'],
                permit_info['site_fill'],
                permit_info['total_dwelling_units'],
                permit_info['dwelling_units_occupied'],
                permit_info['fee_type'],
                convert_date(permit_info['filing_date']),
                permit_info['status'],
                convert_date(permit_info['proposed_job_start']),
                convert_date(permit_info['work_approved']),
                permit_info['work_description'],
                permit_info['job_number'],
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
        print("🧐 Rate limited - switching to remote scraper")
        remaining = MAX_SUCCESSFUL_LINKS - successful_links_opened
        remote_scraper(remaining)
    elif result == "error":
        print("⚠️ Script ended with an error. Investigate.")
    else:
        print("✅ Scraper finished successfully.")
