from dotenv import load_dotenv
load_dotenv()

import os
import time
import random
import re
import subprocess
import shutil
from datetime import datetime
from bs4 import BeautifulSoup
import mysql.connector
import psycopg2
import psycopg2.extras
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# Database configuration
DB_TYPE = os.getenv('DB_TYPE', 'postgresql')  # Default to PostgreSQL (Railway)


def find_chromedriver():
    """Find ChromeDriver in common locations"""
    paths = [
        '/opt/homebrew/bin/chromedriver',
        '/usr/local/bin/chromedriver',
        '/usr/bin/chromedriver',
        shutil.which('chromedriver'),
    ]
    
    for path in paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_chrome():
    """Find Chrome/Chromium binary"""
    paths = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/usr/bin/google-chrome',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        shutil.which('google-chrome'),
        shutil.which('chromium'),
    ]
    
    for path in paths:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def get_chrome_version(chrome_path):
    """Get Chrome major version number"""
    if not chrome_path:
        return None
    
    try:
        result = subprocess.run([chrome_path, '--version'], capture_output=True, text=True, timeout=5)
        match = re.search(r'(\d+)\.\d+\.\d+\.\d+', result.stdout)
        return int(match.group(1)) if match else None
    except:
        return None


def create_driver(chrome_path, chromedriver_path, chrome_version):
    """Create and configure Chrome driver"""
    options = uc.ChromeOptions()
    
    if chrome_path:
        options.binary_location = chrome_path
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    
    kwargs = {
        'options': options,
        'version_main': chrome_version or 142,
        'use_subprocess': False
    }
    
    if chromedriver_path:
        kwargs['driver_executable_path'] = chromedriver_path
    if chrome_path:
        kwargs['browser_executable_path'] = chrome_path
    
    driver = uc.Chrome(**kwargs)
    
    # Hide automation markers
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """
    })
    
    return driver


def get_db_connection():
    """Get database connection based on DB_TYPE"""
    if DB_TYPE == 'postgresql':
        return psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5432')),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME', 'railway')
        )
    else:  # mysql
        return mysql.connector.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '3306')),
            user=os.getenv('DB_USER', 'scraper_user'),
            password=os.getenv('DB_PASSWORD'),
            database=os.getenv('DB_NAME', 'permit_scraper')
        )


def get_db_config():
    """Get latest search config from database"""
    conn = get_db_connection()
    
    if DB_TYPE == 'postgresql':
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM permit_search_config ORDER BY created_at DESC LIMIT 1")
    config = cursor.fetchone()
    
    if DB_TYPE == 'postgresql':
        return conn, cursor, {
            'month': config['start_month'],
            'day': config['start_day'],
            'year': config['start_year'],
            'type': config['permit_type']
        }
    else:
        return conn, cursor, {
            'month': config[1],
            'day': config[2],
            'year': config[3],
            'type': config[4]
        }


def get_or_create_job(cursor, conn, config):
    """Get existing job or create new one"""
    cursor.execute("""
        SELECT id FROM contact_scrape_jobs
        WHERE permit_type = %s AND start_month = %s AND start_day = %s AND start_year = %s
        ORDER BY created_at DESC LIMIT 1
    """, (config['type'], config['month'], config['day'], config['year']))
    
    result = cursor.fetchone()
    if result:
        if DB_TYPE == 'postgresql':
            job_id = result['id'] if isinstance(result, dict) else result[0]
        else:
            job_id = result[0]
        print(f"Using existing job ID: {job_id}")
        return job_id
    
    cursor.execute("""
        INSERT INTO contact_scrape_jobs (permit_type, start_month, start_day, start_year)
        VALUES (%s, %s, %s, %s)
    """, (config['type'], config['month'], config['day'], config['year']))
    conn.commit()
    
    if DB_TYPE == 'postgresql':
        cursor.execute("SELECT lastval()")
    else:
        cursor.execute("SELECT LAST_INSERT_ID()")
    
    job_id = cursor.fetchone()[0]
    print(f"Created new job ID: {job_id}")
    return job_id


def extract_permits_from_page(driver):
    """Extract permit data from current page"""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    rows = soup.select("body > center > table:nth-of-type(3) > tbody > tr")
    permits = []
    
    for row in rows:
        cols = row.find_all("td")
        if len(cols) != 7 or "APPLICANT" in cols[0].get_text().upper():
            continue
        
        permit_link = cols[1].find("a")
        link = f"https://a810-bisweb.nyc.gov/bisweb/{permit_link['href']}" if permit_link else ""
        
        permit_data = [col.get_text(strip=True).replace('\xa0', ' ') for col in cols]
        permit_data.append(link)
        permits.append(permit_data)
    
    return permits


def save_permits(cursor, conn, job_id, permits):
    """Save permits to database, skipping duplicates"""
    inserted = 0
    
    for permit in permits:
        try:
            applicant, permit_no, job_type, issue_date, exp_date, bin_no, address, link = permit
            
            cursor.execute("SELECT 1 FROM permits WHERE permit_no = %s", (permit_no,))
            if cursor.fetchone():
                continue
            
            # Convert dates
            try:
                issue_date = datetime.strptime(issue_date, "%m/%d/%Y").strftime("%Y-%m-%d")
            except:
                issue_date = None
            
            try:
                exp_date = datetime.strptime(exp_date, "%m/%d/%Y").strftime("%Y-%m-%d")
            except:
                exp_date = None
            
            cursor.execute("""
                INSERT INTO permits (job_id, applicant, permit_no, job_type, issue_date, exp_date, bin, address, link)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (job_id, applicant, permit_no, job_type, issue_date, exp_date, bin_no, address, link))
            inserted += 1
        except Exception as e:
            print(f"Error saving permit {permit_no}: {e}")
    
    conn.commit()
    if inserted > 0:
        print(f"Saved {inserted} new permits")
    return inserted


def go_to_next_page(driver):
    """Click next button, return False if no more pages"""
    try:
        next_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/center/table[4]/tbody/tr/td[3]/form/input[1]'))
        )
        next_btn.click()
        time.sleep(random.uniform(2, 4))
        return True
    except:
        return False


def run_scraper():
    """Main scraper function"""
    driver = None
    conn = None
    
    try:
        # Setup
        chrome_path = find_chrome()
        chromedriver_path = find_chromedriver()
        chrome_version = get_chrome_version(chrome_path)
        
        print(f"Chrome: {chrome_path or 'auto'}")
        print(f"ChromeDriver: {chromedriver_path or 'auto'}")
        print(f"Version: {chrome_version or 'auto'}")
        
        # Get database config
        conn, cursor, config = get_db_config()
        job_id = get_or_create_job(cursor, conn, config)
        
        print(f"Searching: {config['month']}/{config['day']}/{config['year']} - Type: {config['type']}")
        
        # Create driver
        driver = create_driver(chrome_path, chromedriver_path, chrome_version)
        wait = WebDriverWait(driver, 10)
        
        # Open search page
        driver.get('https://a810-bisweb.nyc.gov/bisweb/bispi00.jsp')
        time.sleep(random.uniform(2, 4))
        
        # Wait for form and fill it
        wait.until(EC.presence_of_element_located((By.ID, 'allstartdate_month')))
        
        Select(driver.find_element(By.ID, 'allstartdate_month')).select_by_value(f"{int(config['month']):02}")
        time.sleep(random.uniform(0.35, 2.65))
        
        day_field = driver.find_element(By.ID, 'allstartdate_day')
        day_str = f"{int(config['day']):02}"
        for char in day_str:
            day_field.send_keys(char)
            time.sleep(random.uniform(0.08, 0.25))
        
        driver.find_element(By.ID, 'allstartdate_year').send_keys(f"{config['year']}")
        time.sleep(random.uniform(0.35, 3.14))
        
        Select(driver.find_element(By.ID, 'allpermittype')).select_by_value(config['type'])
        time.sleep(random.uniform(0.35, 3.14))
        
        # Submit search
        driver.find_element(By.XPATH, "/html/body/div/table[2]/tbody/tr[20]/td/table/tbody/tr/td[2]/table/tbody/tr[2]/td[2]/input").click()
        time.sleep(random.uniform(2, 4))
        
        # Scrape all pages
        total_saved = 0
        page = 1
        
        while True:
            print(f"Scraping page {page}...")
            permits = extract_permits_from_page(driver)
            saved = save_permits(cursor, conn, job_id, permits)
            total_saved += saved
            
            if not go_to_next_page(driver):
                print("No more pages")
                break
            
            page += 1
        
        # Update job with total count
        cursor.execute("SELECT COUNT(*) FROM permits WHERE job_id = %s", (job_id,))
        total_permits = cursor.fetchone()[0]
        cursor.execute("UPDATE contact_scrape_jobs SET total_permits = %s WHERE id = %s", (total_permits, job_id))
        conn.commit()
        
        print(f"✅ Done. Total new permits: {total_saved}, Total in job: {total_permits}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            driver.quit()
        if conn:
            conn.close()


if __name__ == '__main__':
    run_scraper()
