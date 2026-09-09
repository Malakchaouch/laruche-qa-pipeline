"""WebDriver factory: local Chromium for dev/CI, Remote for the Selenium Grid."""
from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions

from .devices import get_profile, mobile_emulation_options


def make_driver(
    remote_url: str | None = None,
    headless: bool = True,
    window: tuple[int, int] = (1280, 900),
    viewport: str | None = None,
):
    """
    remote_url=None        -> local chromedriver (dev machine / CI container)
    remote_url="http://localhost:4444"
                           -> Selenium Grid standalone-chromium container;
                              watch it live on noVNC at :7900.

    viewport=None or "desktop"
                           -> unchanged behaviour, `window` is used as before
    viewport="pixel-7"     -> Chrome mobile emulation: the profile's size,
                              pixel ratio, touch flag and user agent.

    The user agent matters as much as the size: a site that switches layout by
    sniffing the UA would still serve its desktop view if only the window were
    resized, and the test would prove nothing. See executor/devices.py.
    """
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")

    emulation = mobile_emulation_options(viewport) if viewport else None
    if emulation:
        # deviceMetrics drives the CSS viewport, so the window is sized to
        # match; otherwise the page renders mobile inside a desktop-sized
        # screenshot, which makes the evidence misleading.
        p = get_profile(viewport)
        window = (p["width"], p["height"])
        opts.add_experimental_option("mobileEmulation", emulation)

    opts.add_argument(f"--window-size={window[0]},{window[1]}")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    if remote_url:
        return webdriver.Remote(command_executor=remote_url, options=opts)
    return webdriver.Chrome(options=opts)
