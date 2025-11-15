from dotenv import load_dotenv
load_dotenv()

import os
import time
import random
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime
from fake_useragent import UserAgent
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# -------------------- CONFIG --------------------
REMOTE_WEBDRIVER_URL = os.getenv("REMOTE_WEBDRIVER_URL")
MAX_RATE_LIMITS = 3

# Proxy configuration - pick one proxy per session
PROXY_PORTS = [10001, 10002, 10003, 10004, 10005, 10006, 10007, 10008, 10009]
PROXY_PORT = random.choice(PROXY_PORTS)
PROXY_HOST = os.getenv('PROXY_HOST', 'gate.decodo.com')
PROXY_USER = os.getenv('PROXY_USER', 'spckyt8xpj')
PROXY_PASS = os.getenv('PROXY_PASS', 'r~P6RwgDe6hjh6jb6W')

print(f"🔀 Remote scraper using proxy port: {PROXY_PORT}")

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
    
    # Configure proxy
    proxy_string = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_HOST}:{PROXY_PORT}"
    options.add_argument(f'--proxy-server=http://{PROXY_HOST}:{PROXY_PORT}')
    
    # Test proxy before using
    try:
        proxies = {
            "http": proxy_string,
            "https": proxy_string
        }
        response = requests.get("https://ipinfo.io/json", proxies=proxies, timeout=10)
        ip_info = response.json()
        print(f"✅ Remote proxy IP: {ip_info.get('ip')} ({ip_info.get('city')}, {ip_info.get('country')})")
    except Exception as e:
        print(f"⚠️ Proxy test warning: {e}")
    
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
        
        def extract_bbl_info():
            """Extract Block and Lot from table[2]"""
            try:
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
                return {'block': None, 'lot': None}
        
        def extract_permit_details():
            """Extract all permit details to match local scraper"""
            try:
                details = {}
                
                # Field 14: Job Number
                try:
                    job_no_elem = driver.find_element(By.XPATH, "//td[contains(text(), 'Job No:')]/following-sibling::td[1]")
                    details['job_number'] = job_no_elem.text.strip()
                except:
                    details['job_number'] = None
                
                # Field 8: Filing Date
                filing_text = get_val("Filing Date:")
                if filing_text:
                    import re
                    date_match = re.search(r'(\d{2}/\d{2}/\d{4})', filing_text)
                    details['filing_date'] = date_match.group(1) if date_match else None
                else:
                    details['filing_date'] = None
                
                # Field 10: Status
                details['status'] = get_val("Status:")
                
                # Field 7: Fee Type
                details['fee_type'] = get_val("Fee:")
                
                # Field 11: Proposed Job Start
                details['proposed_job_start'] = get_val("Proposed Job Start:")
                
                # Field 12: Work Approved
                details['work_approved'] = get_val("Work Approved:")
                
                # Field 3: Site Fill
                details['site_fill'] = get_val("Site Fill:")
                
                # Field 13: Work Description
                try:
                    work_desc_parts = []
                    work_label = driver.find_element(By.XPATH, "//td[@class='label' and contains(text(), 'Work:')]")
                    parent_row = work_label.find_element(By.XPATH, "./..")
                    following_rows = parent_row.find_elements(By.XPATH, "./following-sibling::tr")
                    
                    for row in following_rows[:5]:
                        cells = row.find_elements(By.TAG_NAME, "td")
                        for cell in cells:
                            if cell.get_attribute("class") == "content":
                                text = cell.text.strip()
                                if text and not text.startswith("--") and len(text) > 5:
                                    work_desc_parts.append(text)
                        if any(cell.get_attribute("class") == "label" for cell in cells):
                            break
                    
                    details['work_description'] = ' '.join(work_desc_parts) if work_desc_parts else None
                except:
                    details['work_description'] = None
                
                # Field 5: Total Dwelling Units
                total_units_text = get_val("Total Number of Dwelling Units at Location:")
                details['total_dwelling_units'] = int(total_units_text) if total_units_text and total_units_text.isdigit() else None
                
                # Field 6: Dwelling Units Occupied
                occupied_text = get_val("Number of Dwelling Units Occupied During Construction:")
                details['dwelling_units_occupied'] = int(occupied_text) if occupied_text and occupied_text.isdigit() else None
                
                return details
            except Exception as e:
                print(f"⚠️ Error extracting permit details: {e}")
                return {}

        bbl_info = extract_bbl_info()
        permit_details = extract_permit_details()
        
        details = {
            "use": get_val("Use:"),
            "stories": get_val("Stories:"),
            "total_units": get_val("Total Number of Dwelling Units at Location:"),
            "occupied_units": get_val("Number of Dwelling Units Occupied During Construction:"),
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
        print(f"⚠️ extract failed: {e}")
        return [], {
            "use": None, "stories": None, "total_units": None, "occupied_units": None,
            "block": None, "lot": None, "site_fill": None, "total_dwelling_units": None,
            "dwelling_units_occupied": None, "fee_type": None, "filing_date": None,
            "status": None, "proposed_job_start": None, "work_approved": None,
            "work_description": None, "job_number": None
        }

def process_permit_page(driver, successful_links_opened, rate_limit_count, cursor, conn):
    first_visit = True
    link_index = 0
    MAX_SUCCESSFUL_LINKS = 999  # Will be checked by caller

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
            link_index += 1
            continue

        permit_id = result['id'] if isinstance(result, dict) else result[0]
        cursor.execute("SELECT 1 FROM contacts WHERE permit_id = %s AND is_checked = TRUE LIMIT 1", (permit_id,))
        if cursor.fetchone():
            print(f"✅ Already checked: {permit_no}")
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

            # Helper function to convert date strings to proper format
            def convert_date(date_str):
                if not date_str or not date_str.strip():
                    return None
                try:
                    from datetime import datetime
                    return datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
                except:
                    return None

            # Update permits table with all extracted fields
            cursor.execute("""
                UPDATE permits SET
                    use_type = %s,
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
                info['use'], 
                info['stories'], 
                info['total_units'], 
                info['occupied_units'],
                info['block'],
                info['lot'],
                info['site_fill'],
                info['total_dwelling_units'],
                info['dwelling_units_occupied'],
                info['fee_type'],
                convert_date(info['filing_date']),
                info['status'],
                convert_date(info['proposed_job_start']),
                convert_date(info['work_approved']),
                info['work_description'],
                info['job_number'],
                permit_id
            ))
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
    """Remote scraper that connects to database and scrapes permits via remote webdriver"""
    
    # Initialize database connection
    from dotenv import load_dotenv
    load_dotenv()
    
    DB_HOST = os.getenv('DB_HOST', 'maglev.proxy.rlwy.net')
    DB_PORT = os.getenv('DB_PORT', '26571')
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME', 'railway')
    
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        dbname=DB_NAME
    )
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Load config
    cursor.execute("SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1")
    config = cursor.fetchone()
    start_month = config['start_month']
    start_day = config['start_day']
    start_year = config['start_year']
    permit_type = config['permit_type']
    MAX_SUCCESSFUL_LINKS = config.get('max_successful_links', random.randint(7, 20))
    
    print(f"📋 Remote scraper config: {start_month}/{start_day}/{start_year}, type={permit_type}")
    print(f"🔀 Target: {number_of_permits_to_scrape} permits")
    
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
            successful_links_opened, rate_limit_count = process_permit_page(driver, successful_links_opened, rate_limit_count, cursor, conn)
            if rate_limit_count >= MAX_RATE_LIMITS:
                break
            if not go_to_next_page(driver):
                break

    except Exception as e:
        print(f"❌ Remote scraper failed: {e}")

    finally:
        print(f"🎯 Done. Opened {successful_links_opened}, rate-limit hits: {rate_limit_count}")
        driver.quit()
        cursor.close()
        conn.close()

#remote_scraper()