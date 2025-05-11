import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# Set up Chrome options
options = Options()
options.add_argument("--start-maximized")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")

# Create driver
driver = webdriver.Chrome(options=options)
driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
wait = WebDriverWait(driver, 10)

def get_table_data(driver):
    """Parses the permit table and returns formatted data rows."""
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    tbody = soup.select_one("body > center > table:nth-of-type(3) > tbody")
    if not tbody:
        return []

    rows = tbody.find_all("tr")
    table_data = []
    for row in rows:
        cols = row.find_all("td")
        text = [col.get_text(strip=True).replace('\xa0', ' ') for col in cols]
        if len(text) == 7:  # Skip header
            table_data.append(text)
    return table_data

def go_to_next_page(driver):
    """Attempts to go to the next page. Returns False if no link is found."""
    try:
        next_link = driver.find_element(By.LINK_TEXT, 'Next')
        next_link.click()
        time.sleep(1.5)
        return True
    except:
        return False

def format_and_print(data):
    print(f"{'APPLICANT':<25} {'PERMIT NO.':<25} {'JOB TYPE':<10} {'ISSUE DATE':<12} {'EXP. DATE':<12} {'BIN':<10} {'ADDRESS'}")
    print("=" * 120)
    for row in data:
        print(f"{row[0]:<25} {row[1]:<25} {row[2]:<10} {row[3]:<12} {row[4]:<12} {row[5]:<10} {row[6]}")

try:
    driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')

    # Fill out the form
    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
    driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
    driver.find_element(By.ID, 'allstartdate_year').send_keys('2025')
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')

    # Submit form
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
    time.sleep(2)

    all_data = []
    while True:
        wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/center/table[3]/tbody')))
        data = get_table_data(driver)
        all_data.extend(data)
        if not go_to_next_page(driver):
            break

    format_and_print(all_data)
    input("Press Enter to exit...")

except Exception as e:
    print("Error:", e)
    input("Press Enter to exit...")

finally:
    driver.quit()
