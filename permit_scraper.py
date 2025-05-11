import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# Set up Chrome options to look less like a bot
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

search_from_year = '2025'

try:
    driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')

    # Wait until the form loads
    wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))

    # Select "March"
    Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value("03")
    time.sleep(0.2)

    # Enter Day and Year
    driver.find_element(By.ID, 'allstartdate_day').send_keys('1')
    driver.find_element(By.ID, 'allstartdate_year').send_keys(search_from_year)

    # Select Permit Type: NB (New Building)
    Select(driver.find_element(By.ID, 'allpermittype')).select_by_value('NB')
    time.sleep(2)

    # Click the "GO" button
    driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()

    try:
        wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/center/table[3]/tbody')))
    except:
        input("Press go yourself then click enter: ")

    # Wait for the table to load
    wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/center/table[3]/tbody')))
    time.sleep(.5)

    # Parse table with BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    tbody = soup.select_one("body > center > table:nth-of-type(3) > tbody")
    rows = tbody.find_all("tr")

    # Extract data
    table_data = []
    for row in rows:
        cols = row.find_all("td")
        text = [col.get_text(strip=True).replace('\xa0', ' ') for col in cols]
        if len(text) == 7:  # Skip header
            table_data.append(text)

    # Print formatted output
    print(f"{'APPLICANT':<25} {'PERMIT NO.':<25} {'JOB TYPE':<10} {'ISSUE DATE':<12} {'EXP. DATE':<12} {'BIN':<10} {'ADDRESS'}")
    print("=" * 120)
    for row in table_data:
        print(f"{row[0]:<25} {row[1]:<25} {row[2]:<10} {row[3]:<12} {row[4]:<12} {row[5]:<10} {row[6]}")

    input("Press Enter to exit...")



except Exception as e:
    print("Error:", e)
    input("Press Enter to exit...")

finally:
    driver.quit()
