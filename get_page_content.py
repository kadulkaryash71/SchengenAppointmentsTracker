import requests
from pathlib import Path

url = "https://schengenappointments.com/in/dublin/tourism"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}

resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)

print("Status:", resp.status_code)
print("Final URL:", resp.url)
print("Content-Type:", resp.headers.get("Content-Type"))
print("Length:", len(resp.text))
print()
print(resp.text[:2000])

Path("debug_schengen_page.html").write_text(resp.text, encoding="utf-8")

markers = [
    "cloudflare",
    "captcha",
    "attention required",
    "verify you are human",
    "cf-chl",
    "just a moment",
    "denmark",
    "countries below have no available slots",
]

print("\nMarker check:")
lower = resp.text.lower()
for m in markers:
    print(f"{m!r}: {m in lower}")