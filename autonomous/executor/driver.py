"""WebDriver factory: local Chromium for dev/CI, Remote for the Selenium Grid."""

from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions


def make_driver(
    remote_url: str | None = None,
    headless: bool = True,
    window: tuple[int, int] = (1280, 900),
):
    """
    remote_url=None        -> local chromedriver (dev machine / CI container)
    remote_url="http://localhost:4444"
                           -> Selenium Grid standalone-chromium container;
                              watch it live on noVNC at :7900.
    """
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={window[0]},{window[1]}")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    if remote_url:
        return webdriver.Remote(command_executor=remote_url, options=opts)
    return webdriver.Chrome(options=opts)
