import time
import random
from bs4 import BeautifulSoup
import mysql.connector
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime
from fake_useragent import UserAgent
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# DB Connection
conn = mysql.connector.connect(
    host='localhost',
    user='scraper_user',
    password='Tyemakharadze9',
    database='permit_scraper'
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

rate_limit_count = 0  # Track how many times the scraper was rate-limited

# Human-like Behavior
def human_delay(min_sec=2.0, max_sec=4.0):
    time.sleep(random.uniform(min_sec, max_sec))

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

# Create Stealth Driver with rotating identity
def create_driver():
    # Bright Data Residential credentials
    proxy_host = "brd.superproxy.io"
    proxy_port = 33335
    proxy_user = "brd-customer-hl_4339d789-zone-residential_proxy1"
    proxy_pass = "poz01me1nve3"

    proxy = f"{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
    proxy_argument = f"--proxy-server=http://{proxy}"

    options = uc.ChromeOptions()
    #options.add_argument(proxy_argument)
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"--user-agent={UserAgent().random}")
    options.add_argument(f"--user-data-dir=/tmp/profile-{random.randint(1000, 99999)}")

    driver = uc.Chrome(options=options, service=Service(ChromeDriverManager().install()))

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    })

    return driver





# Detect Access Denied block

def is_access_denied(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        return "Access Denied" in body_text or "You don't have permission" in body_text
    except:
        return False

# Scrape contact names & phones
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

        return people
    except Exception as e:
        print(f"⚠️ Failed to extract name and phone info: {e}")
        return []

# Scrape permit links and details from a single page
def process_permit_page(driver):
    global rate_limit_count

    permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
    print(f"✅ Found {len(permit_links)} permit links.")

    for i in range(len(permit_links)):
        permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
        if i >= len(permit_links):
            break

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
            print(f"➡️ Clicking into permit {permit_no}...")
            link_element.click()
            human_delay()

            # Access Denied check
            if is_access_denied(driver):
                print(f"🚫 Access Denied for permit {permit_no}. Restarting driver...")
                rate_limit_count += 1
                driver.quit()
                driver = create_driver()
                wait = WebDriverWait(driver, 10)
                driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
                human_delay()
                Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
                driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
                driver.find_element(By.ID, 'allstartdate_year').send_keys('2025')
                Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')
                driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
                human_delay()
                return  # retry the page from top

            contacts = extract_names_and_phones(driver)

            if not contacts:
                cursor.execute("""
                    INSERT INTO contacts (permit_id, is_checked)
                    VALUES (%s, %s)
                """, (permit_id, True))
                conn.commit()
                print(f"📂 No contacts found, but marked as checked for permit {permit_no}")
                driver.back()
                human_delay()
                continue

            for name, phone in contacts:
                cursor.execute("""
                    INSERT INTO contacts (permit_id, name, phone, is_checked)
                    VALUES (%s, %s, %s, %s)
                """, (permit_id, name, phone, True))
            conn.commit()
            print(f"📅 Saved {len(contacts)} contact(s) for permit {permit_no}")

        except Exception as e:
            print(f"⚠️ Rate limit or error on permit {permit_no}: {e}")
            rate_limit_count += 1
            print(f"Limit Counter: {rate_limit_count}")
            driver.quit()
            print("🔄 Restarting driver...")
            driver = create_driver()
            wait = WebDriverWait(driver, 10)
            driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
            human_delay()
            Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
            driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
            driver.find_element(By.ID, 'allstartdate_year').send_keys('2025')
            Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')
            driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
            human_delay()
            return

        print("🔙 Going back to results page...")
        driver.back()
        human_delay()


# Attempt to go to the next page
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

# Main Scraping Logic
try:
    driver = create_driver()
    wait = WebDriverWait(driver, 10)

    print("🟡 Opening search form...")
    driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
    human_delay()

    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value(f"{int(start_month):02}")
    driver.find_element(By.ID, 'allstartdate_day').send_keys(f"{int(start_day):02}")
    driver.find_element(By.ID, 'allstartdate_year').send_keys(start_year)
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value(permit_type)
    human_delay()
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
    human_delay()

    while True:
        process_permit_page(driver)
        if not go_to_next_page(driver):
            print("🔚 No more pages. Done.")
            break

except Exception as e:
    print(f"❌ Script failed: {e}")

finally:
    print(f"🔝 Done. Total rate limit evasions: {rate_limit_count}")
    driver.quit()
    cursor.close()
    conn.close()
