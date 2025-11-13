from dotenv import load_dotenv
load_dotenv()

import os
import time
import random
import requests
import mysql.connector
from datetime import datetime
from fake_useragent import UserAgent
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# -------------------- CONFIG --------------------
REMOTE_WEBDRIVER_URL = os.getenv("REMOTE_WEBDRIVER_URL")
MAX_SUCCESSFUL_LINKS = random.randint(7, 20)
MAX_RATE_LIMITS = 3

# -------------------- DATABASE --------------------
conn = mysql.connector.connect(
    host=os.getenv('DB_HOST'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME')
)
cursor = conn.cursor()
cursor.execute("SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1")
config = cursor.fetchone()
start_month, start_day, start_year, permit_type, contact_search_limit = config[1], config[2], config[3], config[4], config[9]
print(f"📋 Loaded config: start={start_month}/{start_day}/{start_year}, type={permit_type}")
print(f"🔀 Looking for {MAX_SUCCESSFUL_LINKS} contacts this run.")

# -------------------- UTILS --------------------
def human_delay(min_sec=2.0, max_sec=10.0):
    s = random.uniform(min_sec, max_sec)
    print(f"⏱️ Sleeping {s:.2f}s to mimic human behavior")
    time.sleep(s)

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

# -------------------- SELENIUM DRIVER --------------------
def create_driver():
    options = webdriver.ChromeOptions()
    driver = webdriver.Remote(
        command_executor=REMOTE_WEBDRIVER_URL,
        options=options
    )
    return driver

# -------------------- SCRAPING FUNCTIONS --------------------
def is_access_denied(driver):
    try:
        return "Access Denied" in driver.find_element(By.TAG_NAME, "body").text
    except:
        return False

def extract_names_and_phones(driver):
    try:
        table = driver.find_element(By.XPATH, "/html/body/center/table[7]")
        soup = BeautifulSoup(table.get_attribute("outerHTML"), "html.parser")
        people, cur = [], None

        for tr in soup.find_all('tr'):
            text = tr.get_text(" ", strip=True)
            for label in ["Issued to:", "Superintendent of Construction:", "Site Safety Manager:", "Business:"]:
                if label in text:
                    cur = text.replace(label, "").strip()
            if "Phone:" in text and cur:
                phone = text.split("Phone:")[-1].strip()
                people.append((cur, phone))
                cur = None

        def get_val(lab):
            try:
                cell = driver.find_element(By.XPATH, f"//td[contains(text(), '{lab}')]")
                return cell.find_element(By.XPATH, "./following-sibling::td[1]").text.strip()
            except:
                return None

        details = {
            "use": get_val("Use:"),
            "stories": get_val("Stories:"),
            "total_units": get_val("Total Number of Dwelling Units at Location:"),
            "occupied_units": get_val("Number of Dwelling Units Occupied During Construction:")
        }
        return people, details

    except Exception as e:
        print(f"⚠️ extract failed: {e}")
        return [], {k: None for k in ["use", "stories", "total_units", "occupied_units"]}

def process_permit_page(driver, successful_links_opened, rate_limit_count):
    first_visit = True
    link_index = 0

    while True:
        links = driver.find_elements(By.XPATH, "//table[3]//a[contains(@href,'WorkPermitDataServlet')]")
        if link_index >= len(links):
            print("📭 No more permits found on page.")
            break

        if first_visit:
            print(f"🔍 {len(links)} permits listed.")
            first_visit = False

        if successful_links_opened >= MAX_SUCCESSFUL_LINKS or rate_limit_count >= MAX_RATE_LIMITS:
            return successful_links_opened, rate_limit_count

        link = links[link_index]
        permit_no = link.text.strip()

        cursor.execute("SELECT id FROM permits WHERE permit_no = %s", (permit_no,))
        result = cursor.fetchone()
        if not result:
            print(f"❌ Skipping untracked permit: {permit_no}")
            cursor.execute("INSERT INTO contacts (permit_id, name, phone, is_checked) VALUES (NULL, 'SKIPPED_UNTRACKED', %s, TRUE)", (permit_no,))
            conn.commit()
            link_index += 1
            continue

        permit_id = result[0]
        cursor.execute("SELECT 1 FROM contacts WHERE permit_id = %s AND is_checked = TRUE LIMIT 1", (permit_id,))
        if cursor.fetchone():
            print(f"✅ Already checked: {permit_no}")
            cursor.execute("INSERT INTO contacts (permit_id, name, phone, is_checked) VALUES (%s, 'SKIPPED_ALREADY_CHECKED', NULL, TRUE)", (permit_id,))
            conn.commit()
            link_index += 1
            continue

        try:
            print(f"↪ Clicking permit {permit_no}")
            driver.execute_script("arguments[0].setAttribute('target','_self')", link)
            link.click()
            human_delay()

            if is_access_denied(driver):
                print("🚫 Access denied — skipping")
                rate_limit_count += 1
                if rate_limit_count >= MAX_RATE_LIMITS:
                    print("❌ Hit max rate limits. Stopping early.")
                    return successful_links_opened, rate_limit_count
                driver.back()
                human_delay()
                link_index += 1
                continue

            people, info = extract_names_and_phones(driver)
            print("📝 Permit info:", info)

            if not people:
                cursor.execute("INSERT INTO contacts (permit_id, is_checked) VALUES (%s, TRUE)", (permit_id,))
                conn.commit()
            else:
                for name, phone in people:
                    cursor.execute("""
                        INSERT INTO contacts (permit_id, name, phone, is_checked)
                        VALUES (%s, %s, %s, TRUE)
                    """, (permit_id, name, phone))
                conn.commit()

            cursor.execute("""
                UPDATE permits SET
                    use_type = %s,
                    stories = %s,
                    total_units = %s,
                    occupied_units = %s
                WHERE id = %s
            """, (info['use'], info['stories'], info['total_units'], info['occupied_units'], permit_id))
            conn.commit()

            successful_links_opened += 1

        except Exception as e:
            print(f"⚠️ Error processing {permit_no}: {e}")
            rate_limit_count += 1
            if rate_limit_count >= MAX_RATE_LIMITS:
                print("❌ Hit max rate limits. Stopping early.")
                return successful_links_opened, rate_limit_count

        driver.back()
        human_delay()
        link_index += 1

    return successful_links_opened, rate_limit_count

def go_to_next_page(driver):
    try:
        print("➡️ Trying to go to next page...")
        next_button = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "/html/body/center/table[4]/tbody/tr/td[3]/form/input[1]"))
        )
        driver.execute_script("arguments[0].scrollIntoView({ behavior: 'smooth', block: 'center' });", next_button)
        human_delay(1.2, 2.6)
        next_button.click()
        print("✅ Clicked next page.")
        return True
    except Exception as e:
        print(f"❌ Couldn't click next page: {e}")
        with open("dob_debug_dump.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("📝 Dumped current HTML to dob_debug_dump.html")
        return False

def remote_scraper(number_of_permits_to_scrape):
    successful_links_opened = 0
    rate_limit_count = 0

    try:
        driver = create_driver()
        wait = WebDriverWait(driver, 10)

        driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
        human_delay(0.5, 4.5)

        wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
        Select(driver.find_element(By.ID,'allstartdate_month')).select_by_value(f"{int(start_month):02}")
        human_delay(0.3,2.5)

        e_day = driver.find_element(By.ID,'allstartdate_day')
        for char in f"{int(start_day):02}":
            e_day.send_keys(char); human_delay(0.08,0.25)
        driver.find_element(By.ID,'allstartdate_year').send_keys(start_year)

        Select(driver.find_element(By.ID,'allpermittype')).select_by_value(permit_type)
        driver.find_element(By.NAME, 'go13').click()

        human_delay()

        while successful_links_opened < number_of_permits_to_scrape and rate_limit_count < MAX_RATE_LIMITS:
            successful_links_opened, rate_limit_count = process_permit_page(driver, successful_links_opened, rate_limit_count)
            if rate_limit_count >= MAX_RATE_LIMITS:
                break
            if not go_to_next_page(driver):
                break

    except Exception as e:
        print(f"❌ Script failed: {e}")

    finally:
        print(f"🎯 Done. Opened {successful_links_opened}, rate-limit hits: {rate_limit_count}")
        driver.quit()
        cursor.close()
        conn.close()

#remote_scraper()
