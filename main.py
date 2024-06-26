import atexit
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import math
import os
from re import T
import signal
from threading import Lock
import time
import phonenumbers
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from fake_useragent import UserAgent
from seleniumwire import webdriver
import chromedriver_binary

# Global variables
price_range = 100  # Price range for filtering products
request_timeout = 0  # Timeout for requests (in seconds)
no_proxy_request_timeout = 20  # Timeout for requests (in seconds)
max_retries = 5  # Maximum number of retries for failed requests

proxy_filename = 'proxy.txt'  # Filename for storing proxies
csv_filename = 'phone_numbers.csv'  # Filename for storing phone numbers
progress_filename = 'progress.json'  # Filename for storing progress

use_headless_drivers = True  # Use headless drivers for increased performance
use_user_agent_rotation = True  # Rotate user agents for increased performance

# Initialize global variables
proxy_list = []
last_non_proxy_attempt = time.time() - 60

def init_driver(headless=True, user_agent_rotation=False, advanced_stealth=False):
    options = webdriver.ChromeOptions()
    
    if headless:
        options.add_argument("--headless=new")

    # Disable images
    options.add_argument("--blink-settings=imagesEnabled=false")

    # Disable JavaScript
    options.add_argument("--disable-javascript")

    # Disable GPU
    options.add_argument("--disable-gpu")

    # Disable notifications
    options.add_argument("--disable-notifications")

    # Disable automatic downloads
    options.add_argument("--disable-downloads")

    # Disable infobars
    options.add_argument("--log-level=3")

    # Additional arguments to improve performance
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-features=NetworkService")
    options.add_argument("--window-size=1920,1080")

    options.add_experimental_option("excludeSwitches", ['enable-logging'])

    # User agent rotation (optional)
    if user_agent_rotation:
        ua = UserAgent()
        user_agent = ua.random
        options.add_argument(f"user-agent={user_agent}")

    # Advanced stealth (optional)
    if advanced_stealth:
        # Install required libraries (e.g., antidetect_selenium)
        driver = None  # Replace with 'edge' as needed
    else:
        driver = webdriver.Chrome(options=options)

    return driver

def create_driver_pool(max_workers):
    driver_pool = []
    lock = Lock()  # Create a lock for thread-safe driver access
    printProgressBar(0, max_workers, prefix = 'Progress:', suffix = 'Complete', length = 50)
    for worker in range(max_workers):
        driver = init_driver(headless=use_headless_drivers, user_agent_rotation=use_user_agent_rotation)
        printProgressBar(worker + 1, max_workers, prefix = 'Progress:', suffix = 'Complete', length = 50)
        with lock:
            driver_pool.append(driver)

    return driver_pool, lock

def rotate_driver(driver_pool, lock):
    with lock:
        driver = driver_pool.pop(0)
        driver_pool.append(driver)
    return driver
    
def read_proxies_from_file(file_path):
    proxy_list = []
    with open(file_path, 'r') as file:
        for line in file:
            proxy = line.strip()
            if proxy:
                proxy_list.append(proxy)

    return proxy_list

def proxy_to_proxy_object(proxy):
    proxy_parts = proxy.split(':')
    proxy_host = proxy_parts[0]
    proxy_port = proxy_parts[1]
    proxy_user = proxy_parts[2]
    proxy_pass = proxy_parts[3]
    proxy_url = f"http://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
    return {
        "http": proxy_url,
        "https": proxy_url
    }

def get_proxy():
    if not proxy_list:
        return None
    
    proxy = proxy_list[0]
    proxy_list.append(proxy_list.pop(0))
    return proxy_to_proxy_object(proxy)

def printProgressBar (iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    """
    Call in a loop to create terminal progress bar
    @params:
        iteration   - Required  : current iteration (Int)
        total       - Required  : total iterations (Int)
        prefix      - Optional  : prefix string (Str)
        suffix      - Optional  : suffix string (Str)
        decimals    - Optional  : positive number of decimals in percent complete (Int)
        length      - Optional  : character length of bar (Int)
        fill        - Optional  : bar fill character (Str)
        printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
    """
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    print(f'\r{prefix} |{bar}| {percent}% {suffix}', end = printEnd)
    # Print New Line on Complete
    if iteration == total: 
        print()

def fetch_url_with_retry(url, driver, proxy=True):
    for _ in range(max_retries):
        try:
            if proxy:
                driver.proxy = get_proxy()
            else:
                driver.proxy = { }

            time.sleep(request_timeout)
            driver.get(url)
            
            if driver.last_request.response:
                # Check for 404 status code before returning
                if driver.last_request.response.status_code == 404:
                    return None  # Return None for 404
            
                # Check for 429 status code before returning
                if driver.last_request.response.status_code == 429:
                    raise Exception(f"Received 429 status code")
            
            html_source = driver.page_source
            
            if html_source:
                driver.proxy = { }
                return html_source
            else:
                raise Exception(f"Received empty HTML source")
        except Exception as e:
            proxy = True
            print(f"Error: {e}")

    else:
        print(f"Failed to fetch URL {url} after {max_retries} retries.")
        if proxy:
            driver.proxy = { }
        return None
    
def fetch_url_with_retry_and_proxy_fallback(url, driver):
    global last_non_proxy_attempt
    if time.time() - last_non_proxy_attempt > no_proxy_request_timeout:
        html_source = fetch_url_with_retry(url, driver, False)
        last_non_proxy_attempt = time.time()
        if html_source:
            return html_source
        
    html_source = fetch_url_with_retry(url, driver, True)
    return html_source
    
def get_category_name(html_source):
    if not html_source:
        return []

    soup = BeautifulSoup(html_source, 'html.parser')

    # Find the div with class "allegro.listing.top.grid.container.breadcrumb"
    category_name_div = soup.find('div', {'data-box-name': 'allegro.listing.top.grid.container.breadcrumb'})
    if category_name_div:
        # Find all li elements inside the div
        li_elements = category_name_div.find_all('li')

        # Skip the first li element and get the text values of the remaining li elements
        category_names = [li.text.strip() for li in li_elements[1:]]

        # Join the category names with a space
        return " ".join(category_names)
    return ""

def get_page_count(html_source):
    if not html_source:
        return 0
    
    soup = BeautifulSoup(html_source, 'html.parser')

    # Find the div with aria-label="paginacja"
    page_count_element = soup.find('div', {'aria-label': 'paginacja'})
    if page_count_element:
        last_link = page_count_element.find_all('a')[-1]
        page_count_text = last_link.text
        page_count = int(page_count_text)
        return page_count
    return 0

def get_offer_urls(html_source):
    if not html_source:
        return []
    
    soup = BeautifulSoup(html_source, 'html.parser')

    # Find the div with data-role="rightItems"
    right_items_div = soup.find('div', {'data-role': 'rightItems'})
    if not right_items_div:
        return []

    # Find all article elements within the right_items_div
    articles = right_items_div.find_all('article', recursive=True)
    if not articles:
        return []

    offer_urls = []

    # Iterate over each article
    for article in articles:
        article_link = article.find('a')
        if article_link:
            relative_href = article_link.get('href')
            full_url = f"{base_url}{relative_href}"
            offer_urls.append(full_url)

    return offer_urls

def extract_phone_numbers(url, driver):
    html_source = fetch_url_with_retry(url, driver)
    if not html_source:
        return []
    
    soup = BeautifulSoup(html_source, 'html.parser')

    # Find the div with class "description" (nested inside other divs)
    description_div = soup.find('div', recursive=True, attrs={'data-box-name': 'Description container'})
    if not description_div:
        return []

    description_text = description_div.get_text(strip=True, separator='\n')

    phone_numbers = find_phone_numbers(description_text)

    # Remove duplicates from the phone_numbers list
    unique_phone_numbers = list(set(phone_numbers))

    return unique_phone_numbers

def scrape_offer(offer_url, category_name, driver):
    phone_numbers = extract_phone_numbers(offer_url, driver)
    print(f"Phone numbers found: {len(phone_numbers)}, URL: {offer_url}")
    save_phone_numbers(category_name, offer_url, phone_numbers)
    
def find_phone_numbers(text):
    phone_numbers = []
    for match in phonenumbers.PhoneNumberMatcher(text, "PL"):
        phone_number = match.number
        if phonenumbers.is_valid_number(phone_number):
            phone_numbers.append(phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    return phone_numbers

def save_phone_numbers(category, offer, phone_numbers):
    with open(csv_filename, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')
        row = [category, offer, ", ".join(phone_numbers)]
        writer.writerow(row)

def save_progress(data):
    with open(progress_filename, "w") as f:
        json.dump(data, f)

def load_progress():
    if os.path.exists(progress_filename):
        with open(progress_filename, "r") as f:
            return json.load(f)
    else:
        return None
    
def load_previous_progress(progress, category_name):
    if progress and category_name == progress.get("category_name"):
        start_price = progress.get("start_price", 0)
        start_page = progress.get("start_page", 1)
        total_offers_scraped = progress.get("total_offers_scraped", 0)
        print(f"Previous progress found with {total_offers_scraped} offers scraped.")
        
        load_progress = input("Do you want to load the previous progress? (y/n) ").lower()
        if load_progress == "y":
            print("Loading previous progress.")
            return start_price, start_page, total_offers_scraped
        else:
            print("Starting fresh.")
            try:
                os.remove(csv_filename)
            except FileNotFoundError:
                pass
            return 0, 1, 0
    else:
        print("No previous progress found or category name does not match.")
        try:
            os.remove(csv_filename)
        except FileNotFoundError:
            pass
        return 0, 1, 0

def exit_handler():
    try:
        print("Closing open drivers...")
        printProgressBar(0, max_workers, prefix = 'Progress:', suffix = 'Complete', length = 50)
        for i, driver in enumerate(driver_pool):
            printProgressBar(i + 1, max_workers, prefix = 'Progress:', suffix = 'Complete', length = 50)
            driver.quit()
    except:
        pass

def signal_handler(signal, frame):
    exit()

if __name__ == "__main__":
    # Register the exit handler
    atexit.register(exit_handler)
    signal.signal(signal.SIGINT, signal_handler)

    base_category_url = input("Enter the base category URL: ")
    base_url = f"{urlparse(base_category_url).scheme}://{urlparse(base_category_url).netloc}"

    try:
        max_workers = int(input("Enter the maximum number of workers (default: all cores): "))
    except:
        max_workers = os.cpu_count()

    proxy_list = read_proxies_from_file(proxy_filename)
    print(f"Proxies loaded: {len(proxy_list)}")

    print(f"Creating driver pool with {max_workers} workers...")
    driver_pool, lock = create_driver_pool(max_workers)

    base_category_html_source = fetch_url_with_retry_and_proxy_fallback(base_category_url, rotate_driver(driver_pool=driver_pool, lock=lock))

    if base_category_html_source:
        # Get the category name
        category_name = get_category_name(base_category_html_source)
        print(f"Category name: {category_name}")

        # Load previous progress
        progress = load_progress()
        start_from_price, start_page, total_offers_scraped = load_previous_progress(progress, category_name)
        
    else:
        print(f"Base URL is invalid: {base_category_url}")
        exit()

    offers_scraped = 0
    start_time = time.time()  # Record the start time

    for start_price in range(start_from_price, 1000000000, price_range):
        # Calculate the end price
        end_price = start_price + price_range

        if start_price > 0:
            start_price += 0.01

        # Construct the category URL with price filter
        price_filter = f"&price_from={start_price:.2f}&price_to={end_price:.2f}"
        category_url = base_category_url + "?order=p" + price_filter

        category_html_source = fetch_url_with_retry_and_proxy_fallback(category_url, rotate_driver(driver_pool=driver_pool, lock=lock))

        # Get the page count
        page_count = get_page_count(category_html_source)
        print(f"Price range: {start_price:.2f} - {end_price:.2f}, Page count: {page_count}, URL: {category_url}\n")

        if page_count == 0:
            continue

        for page_num in range(start_page, page_count + 1):
            if page_num == 1:
                page_html_source = category_html_source
            else:
                page_url = f"{category_url}&p={page_num}"
                page_html_source = fetch_url_with_retry_and_proxy_fallback(page_url, rotate_driver(driver_pool=driver_pool, lock=lock))

            page_start_time = time.time()  # Record the start time for the current page

            # Save progress
            progress = {
                "start_price": math.floor(start_price),
                "start_page": page_num,
                "total_offers_scraped": total_offers_scraped,
                "category_name": category_name
            }
            
            save_progress(progress)
            print("Progress saved.\n")

            # Get the offer URLs
            offer_urls = get_offer_urls(page_html_source)
            print(f"Page {page_num}: {len(offer_urls)} offers found.\n")

            if len(offer_urls) == 0:
                continue

            # Scrape the offers
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                executor.map(lambda url: scrape_offer(url, category_name, rotate_driver(driver_pool=driver_pool, lock=lock)), offer_urls)
            
            offers_scraped += len(offer_urls)
            total_offers_scraped += len(offer_urls)

            # Calculate the elapsed time and offers per minute rate
            end_time = time.time()
            elapsed_time = end_time - start_time
            page_elapsed_time = end_time - page_start_time
            offers_per_minute = offers_scraped / (elapsed_time / 60)

            # Print the results for the current page
            print(f"\nPage {page_num} scraped in {page_elapsed_time:.2f} seconds")
            print(f"Total offers scraped: {total_offers_scraped}")
            print(f"Elapsed time: {elapsed_time:.2f} seconds")
            print(f"Offers per minute: {offers_per_minute:.2f}\n")

            start_page = 1

    end_time = time.time()  # Record the end time
    elapsed_time = end_time - start_time  # Calculate the elapsed time in seconds

    # Calculate the overall offers per minute rate
    overall_offers_per_minute = total_offers_scraped / (elapsed_time / 60)
    print(f"Total offers scraped: {total_offers_scraped}")
    print(f"Elapsed time: {elapsed_time:.2f} seconds")
    print(f"Overall offers per minute: {overall_offers_per_minute:.2f}")     

    exit()
