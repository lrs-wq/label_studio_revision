import json
import re
from urllib.parse import quote

from curl_cffi import requests


def fetch(url: str) -> str:
    response = requests.get(
        url,
        impersonate="chrome136",
        timeout=30,
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    response.raise_for_status()
    return response.text


def pexels(query: str, pages: int = 3) -> None:
    seen = set()
    print(f"### Pexels: {query}")
    for page in range(1, pages + 1):
        url = f"https://www.pexels.com/search/videos/{quote(query)}/"
        if page > 1:
            url += f"?page={page}"
        html = fetch(url)
        links = set(re.findall(r"https://www\.pexels\.com/video/[^\"'?<>]+?-\d+/", html))
        links.update(f"https://www.pexels.com{match}" for match in re.findall(r"/video/[^\"'?<>]+?-\d+/", html))
        new = [link for link in sorted(links) if link not in seen]
        seen.update(links)
        print(f"page {page}: links={len(links)} new={len(new)} total={len(seen)} sample={new[:3]}")


def pixabay(query: str, pages: int = 3) -> None:
    seen_links = set()
    seen_mp4 = set()
    print(f"### Pixabay: {query}")
    for page in range(1, pages + 1):
        url = f"https://pixabay.com/videos/search/{quote(query)}/"
        if page > 1:
            url += f"?pagi={page}"
        html = fetch(url)
        links = set(f"https://pixabay.com{match}" for match in re.findall(r"/videos/[^\"<>]+?-\d+/", html))
        mp4s = set(re.findall(r"https://cdn\.pixabay\.com/video/[^\"<>]+?\.mp4", html))
        new_links = [link for link in sorted(links) if link not in seen_links]
        new_mp4s = [mp4 for mp4 in sorted(mp4s) if mp4 not in seen_mp4]
        seen_links.update(links)
        seen_mp4.update(mp4s)
        print(f"page {page}: links={len(links)} new_links={len(new_links)} mp4={len(mp4s)} new_mp4={len(new_mp4s)} total_links={len(seen_links)} total_mp4={len(seen_mp4)}")
        print(f"sample links={new_links[:3]}")
        print(f"sample mp4={new_mp4s[:2]}")


def commons(query: str) -> None:
    print(f"### Commons: {query}")
    url = (
        "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={quote(query + ' filetype:video')}"
        "&gsrnamespace=6&gsrlimit=20&prop=imageinfo&iiprop=url|mime|size|extmetadata&format=json&origin=*"
    )
    data = requests.get(url, impersonate="chrome136", timeout=30).json()
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        info = page.get("imageinfo", [{}])[0]
        print(page.get("title"), "|", info.get("mime"), "|", info.get("url"))


def main() -> None:
    for query in ["cat eating", "cat drinking water", "cat eating bowl", "kitten eating"]:
        pexels(query, 3)
    for query in ["cat eating", "cat drinking water", "cat food bowl", "kitten eating"]:
        pixabay(query, 3)
    commons("cat eating")
    commons("cat drinking")


if __name__ == "__main__":
    main()
