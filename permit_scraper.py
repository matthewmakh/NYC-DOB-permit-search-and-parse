import time
import random
from bs4 import BeautifulSoup
import mysql.connector
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime

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

def human_delay(min_sec=2.0, max_sec=4.0):
    time.sleep(random.uniform(min_sec, max_sec))

def fix_date_format(date_str):
    try:
        return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

def create_driver():
    options = uc.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
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
    for row in data:
        try:
            applicant, permit_no, job_type, issue_date, exp_date, bin_no, address, link = row

            cursor.execute("SELECT 1 FROM permits WHERE permit_no = %s", (permit_no,))
            if cursor.fetchone():
                continue

            cursor.execute("""
                INSERT INTO permits (
                    applicant, permit_no, job_type, issue_date, exp_date, bin, address, link
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                applicant, permit_no, job_type,
                fix_date_format(issue_date),
                fix_date_format(exp_date),
                bin_no, address, link
            ))
        except Exception as e:
            print("❌ Error inserting permit:", e)

    conn.commit()
    print(f"✅ Inserted {len(data)} permits.")

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

# Main
try:
    driver = create_driver()
    wait = WebDriverWait(driver, 10)

    driver.get("https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp")
    human_delay()

    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
    driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
    driver.find_element(By.ID, 'allstartdate_year').send_keys('2025')
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')
    human_delay()
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
    human_delay()

    while True:
        permits = extract_permits(driver)
        insert_permits(permits)
        if not go_to_next(driver):
            break

except Exception as e:
    print("❌ Script crashed:", e)

finally:
    print("🔚 Done.")
    driver.quit()
    cursor.close()
    conn.close()
