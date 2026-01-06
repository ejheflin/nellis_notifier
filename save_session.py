# save_session.py
"""
Run this once (or whenever your location/session gets reset).
It opens a real browser so you can set your Nellis shopping area/location,
then saves cookies/localStorage into nellis_storage.json.

Install:
  pip install playwright
  playwright install chromium

Run:
  python save_session.py
"""

from playwright.sync_api import sync_playwright

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://nellisauction.com/", wait_until="domcontentloaded")

        print("In the opened browser, set your Shopping Area / Location (e.g., Katy/Houston).")
        input("Press Enter here after you've set it... ")

        context.storage_state(path="nellis_storage.json")
        browser.close()
        print("Saved session to nellis_storage.json")

if __name__ == "__main__":
    main()
