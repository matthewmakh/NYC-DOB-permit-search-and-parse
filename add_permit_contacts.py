import os
os.environ['UC_CHROMEDRIVER_VERSION'] = '136'

import time
import random
from bs4 import BeautifulSoup
import mysql.connector
from datetime import datetime
from fake_useragent import UserAgent
import requests

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

from seleniumwire import undetected_chromedriver as uc  # Use selenium-wire wrapper

# -------------------- CONFIG --------------------

USE_PROXY = True
DECODE_PROXY_PORTS = [10003, 10004, 10005, 10001, 10002]
proxy_index = 0

PROXY_HOST = "gate.decodo.com"
PROXY_USER = "spckyt8xpj"
PROXY_PASS = "r~P6RwgDe6hjh6jb6W"

# -------------------- PRINT CURRENT IP --------------------

try:
    my_ip = requests.get("https://ipinfo.io/json", timeout=10).json().get("ip")
    print(f"🔍 Current Machine IP: {my_ip}")
except Exception as e:
    print(f"⚠️ Could not retrieve local IP: {e}")

# -------------------- DATABASE --------------------

conn = mysql.connector.connect(
    host='localhost',
    user='scraper_user',
    password='Tyemakharadze9',
    database='permit_scraper'
)
cursor = conn.cursor()

cursor.execute("SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1")
config = cursor.fetchone()
start_month, start_day, start_year, permit_type, contact_search_limit = config[1], config[2], config[3], config[4], config[9]
print(f'latest config: {config}')

rate_limit_count = 0
MAX_SUCCESSFUL_LINKS = random.randint(5, 15)
successful_links_opened = 0
print(f"Searching For {MAX_SUCCESSFUL_LINKS} Contacts")

# -------------------- UTILS --------------------

def human_delay(min_sec=2.0, max_sec=240.0):
    x = random.uniform(min_sec, max_sec)
    print(f'Sleeping for {x} Seconds to Simulate Human Behavior')
    time.sleep(x)

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

# -------------------- SELENIUM DRIVER --------------------

def create_driver():
    global proxy_index
    proxy_port = DECODE_PROXY_PORTS[proxy_index % len(DECODE_PROXY_PORTS)]
    proxy_index += 1

    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-agent={UserAgent().random}")
    options.add_argument(f"--user-data-dir=/tmp/profile-{random.randint(1000, 99999)}")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--disable-web-security")

    seleniumwire_options = {}
    if USE_PROXY:
        seleniumwire_options = {
            'proxy': {
                'http': f'http://{PROXY_HOST}:{proxy_port}',
                'https': f'http://{PROXY_HOST}:{proxy_port}',
                'no_proxy': 'localhost,127.0.0.1'
            },
            'auth': (PROXY_USER, PROXY_PASS)
        }
        print(f"🔄 Using Secure Auth Proxy: {PROXY_HOST}:{proxy_port}")

    driver = uc.Chrome(options=options, seleniumwire_options=seleniumwire_options)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    })

    try:
        current_ip = requests.get("https://ipinfo.io/json", timeout=10).json().get("ip")
        print(f"🌐 Public IP Detected: {current_ip}")
    except Exception as e:
        print(f"⚠️ Failed to retrieve public IP: {e}")

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
            link_element.click()
            human_delay()

            if is_access_denied(driver):
                print(f"🚫 Access Denied. Restarting...")
                rate_limit_count += 1
                driver.quit()
                time.sleep(10)
                driver = create_driver()
                wait = WebDriverWait(driver, 10)
                driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
                human_delay()
                return

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
            driver.quit()
            driver = create_driver()
            wait = WebDriverWait(driver, 10)
            driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
            human_delay()
            return

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

try:
    driver = create_driver()
    wait = WebDriverWait(driver, 10)

    print("🟡 Opening search form...")
    driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
    time.sleep(random.uniform(0.35,4.65))

    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
    form_inputs = driver.find_element(By.ID, 'allstartdate_month')
    driver.execute_script("arguments[0].scrollIntoView({ behavior: 'smooth', block: 'center' });", form_inputs)

    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value(f"{int(start_month):02}")
    time.sleep(random.uniform(0.35, 4.65))
    driver.find_element(By.ID, 'allstartdate_day').send_keys(f"{int(start_day):02}")
    time.sleep(random.uniform(0.35, 4.65))
    driver.find_element(By.ID, 'allstartdate_year').send_keys(start_year)
    time.sleep(random.uniform(0.35, 4.65))
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value(permit_type)
    human_delay()
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
    human_delay()

    while True:
        process_permit_page(driver)
        human_delay()
        if not go_to_next_page(driver):
            print("🔚 No more pages.")
            break

except Exception as e:
    print(f"❌ Script failed: {e}")

finally:
    print(f"🔝 Done. Total rate limit evasions: {rate_limit_count}")
    try:
        driver.quit()
        del driver
    except Exception as e:
        print(f"⚠️ Cleanup issue: {e}")
    cursor.close()
    conn.close()