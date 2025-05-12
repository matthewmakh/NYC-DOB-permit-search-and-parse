import time
import random
from bs4 import BeautifulSoup
import mysql.connector
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC


# DB Connection
conn = mysql.connector.connect(
    host='localhost',
    user='scraper_user',
    password='put your password here',
    database='permit_scraper'
)
cursor = conn.cursor()

#Human-like Behavior
def human_delay(min_sec=2.0, max_sec=4.0):
    time.sleep(random.uniform(min_sec, max_sec))


# Stealth Chrome Driver Setup (IP whitelisted proxy only)

def create_stealth_driver():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
    # options.add_argument("--proxy-server=http://gate.decodo.com:10003")  # Only if IP whitelisted

    driver = uc.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """
    })
    return driver


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
                    current_name = None  # Allow next name/phone to pair

        return people
    except Exception as e:
        print(f"⚠️ Failed to extract name and phone info: {e}")
        return []


# ⟳ Main Scraping Logic
try:
    driver = create_stealth_driver()
    wait = WebDriverWait(driver, 10)

    print("🟡 Opening search form...")
    driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
    human_delay()

    # Fill and submit search form
    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
    driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
    driver.find_element(By.ID, 'allstartdate_year').send_keys('2025')
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')
    human_delay()
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
    human_delay()

    # Get all clickable permit links
    permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
    print(f"✅ Found {len(permit_links)} permit links.")

    for i in range(len(permit_links)):
        permit_links = driver.find_elements(By.XPATH, "/html/body/center/table[3]//a[contains(@href, 'WorkPermitDataServlet')]")
        if i >= len(permit_links):
            break

        link_element = permit_links[i]
        permit_no = link_element.text.strip()

        # Lookup permit_id from DB
        cursor.execute("SELECT id FROM permits WHERE permit_no = %s", (permit_no,))
        result = cursor.fetchone()
        if not result:
            print(f"❌ Skipping untracked permit: {permit_no}")
            continue

        permit_id = result[0]

        # Skip if already has contacts
        cursor.execute("SELECT 1 FROM contacts WHERE permit_id = %s LIMIT 1", (permit_id,))
        if cursor.fetchone():
            print(f"✅ Contacts already exist for permit {permit_no}")
            continue

        print(f"➡️ Clicking into permit {permit_no}...")
        link_element.click()
        human_delay()

        contacts = extract_names_and_phones(driver)
        for name, phone in contacts:
            cursor.execute("""
                INSERT INTO contacts (permit_id, name, phone)
                VALUES (%s, %s, %s)
            """, (permit_id, name, phone))
        conn.commit()
        print(f"📅 Saved {len(contacts)} contact(s) for permit {permit_no}")

        print("🔙 Going back to results page...")
        driver.back()
        human_delay()

except Exception as e:
    print(f"❌ Script failed: {e}")

finally:
    print("🔝 Done. Closing browser and DB connection.")
    driver.quit()
    cursor.close()
    conn.close()
