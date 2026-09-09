"""
Device profiles for Chrome's mobile emulation.

The corpus tags 5 scenarios for a native mobile app, but LaRuche has no native
app — only the Vite frontend. Rather than leave the channel empty, we run the
existing web scenarios at phone and tablet viewports.

This is **mobile web**, not native mobile. It catches responsive layout
breakage — a send button pushed off-screen, a composer that collapses, a reply
area that will not scroll — which is a real class of defect and the one that
actually applies to this product. It does not test a native app, and the report
should say so.

Two things are set per profile:

  deviceMetrics  width, height and pixel ratio, so CSS media queries fire the
                 way they would on the device
  userAgent      so any server-side or JS user-agent sniffing takes the mobile
                 path too

Setting only the window size (the cruder approach) triggers media queries but
not user-agent sniffing, so a site that switches layout by UA would still serve
its desktop view and the test would prove nothing.
"""

from __future__ import annotations

from typing import Any

_UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Mobile Safari/537.36"
)
_UA_IOS = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
_UA_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# Chosen to span the range that matters: a small phone (the tightest layout a
# real user will have), a common modern phone, and a tablet.
PROFILES: dict[str, dict[str, Any]] = {
    "desktop": {
        "label": "Desktop 1280x900",
        "width": 1280, "height": 900, "pixel_ratio": 1.0,
        "user_agent": None,          # keep Chrome's own UA
        "touch": False,
    },
    "iphone-se": {
        "label": "iPhone SE (small phone)",
        "width": 375, "height": 667, "pixel_ratio": 2.0,
        "user_agent": _UA_IOS,
        "touch": True,
    },
    "pixel-7": {
        "label": "Pixel 7 (common phone)",
        "width": 412, "height": 915, "pixel_ratio": 2.625,
        "user_agent": _UA_ANDROID,
        "touch": True,
    },
    "iphone-14": {
        "label": "iPhone 14 (common phone)",
        "width": 390, "height": 844, "pixel_ratio": 3.0,
        "user_agent": _UA_IOS,
        "touch": True,
    },
    "ipad": {
        "label": "iPad (tablet)",
        "width": 820, "height": 1180, "pixel_ratio": 2.0,
        "user_agent": _UA_IPAD,
        "touch": True,
    },
}

DEFAULT_PROFILE = "desktop"


def profile_names() -> list[str]:
    return list(PROFILES)


def get_profile(name: str) -> dict[str, Any]:
    """Look up a profile, failing with the list of valid names."""
    key = (name or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        raise ValueError(
            f"unknown viewport '{name}'. Available: {', '.join(PROFILES)}"
        )
    return PROFILES[key]


def mobile_emulation_options(name: str) -> dict[str, Any] | None:
    """Build the dict Chrome expects for `mobileEmulation`.

    Returns None for the desktop profile — no emulation should be applied at
    all, so the browser behaves exactly as it did before this module existed.
    """
    p = get_profile(name)
    if not p["touch"] and p["user_agent"] is None:
        return None
    emulation: dict[str, Any] = {
        "deviceMetrics": {
            "width": p["width"],
            "height": p["height"],
            "pixelRatio": p["pixel_ratio"],
            "touch": p["touch"],
        }
    }
    if p["user_agent"]:
        emulation["userAgent"] = p["user_agent"]
    return emulation


def window_size(name: str) -> tuple[int, int]:
    p = get_profile(name)
    return p["width"], p["height"]
