# -*- coding: utf-8 -*-
"""
Archive.org guide extraction script.
Handles downloading and extracting content from Archive.org guide scans.

1. Fetches metadata from https://archive.org/metadata/{archive_id}
2. Selects best available format: DjVuTXT > OCR Search Text > EPUB
3. Checks if downloads/{archive_id}.txt already exists → skip download
4. If not exists: downloads text, writes to downloads/{archive_id}.txt
5. Fetches subject and collection from metadata
6. Outputs JSON with file_path + metadata to stdout

用法:
    python scripts/archive_extract.py --archive-id "ffvii_official_guide"
    python scripts/archive_extract.py --archive-id "ffvii_official_guide" --debug

输出 JSON 到 stdout，日志到 stderr。
"""

import argparse
import io
import json
import logging
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Vendor fallback ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

_vendor_dir = os.path.join(PROJECT_ROOT, "vendor")
if os.path.isdir(_vendor_dir):
    sys.path.insert(0, _vendor_dir)

try:
    import requests
except ImportError:
    sys.stderr.write("[ERROR] 缺少依赖库 requests\n")
    sys.exit(1)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

METADATA_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60

DOWNLOAD_DIR = os.path.join(PROJECT_ROOT, "downloads")

logger = logging.getLogger("archive_extract")


# ── CLI ──

def parse_args():
    parser = argparse.ArgumentParser(
        description="从 Archive.org 提取攻略书内容并下载到本地"
    )
    parser.add_argument(
        "--archive-id", type=str, required=True,
        help="Archive.org identifier（如 ffvii_official_guide）"
    )
    parser.add_argument(
        "--debug", action="store_true", default=False,
        help="启用调试日志"
    )
    return parser.parse_args()


def setup_logging(debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False


# ── Session ──

def make_session():
    """创建 requests Session，含标准请求头。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xhtml+xml,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    session.verify = False
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
    return session


# ── Format Discovery ──

def fetch_metadata(session, archive_id):
    """Fetch metadata from Archive.org, return file entries, title, subject and collection."""
    url = "https://archive.org/metadata/%s" % archive_id
    logger.info("[Metadata] 获取元数据: %s", url)
    resp = session.get(url, timeout=METADATA_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    files = data.get("files", [])
    metadata = data.get("metadata", {})
    title = metadata.get("title", archive_id)
    subject = metadata.get("subject", [])
    collection = metadata.get("collection", [])
    logger.info("[Metadata] 找到 %d 个文件，标题: %s", len(files), title)
    if subject:
        logger.info("[Metadata] 主题: %s", ", ".join(subject[:5]))
    if collection:
        logger.info("[Metadata] 合集: %s", ", ".join(collection[:5]))
    return files, title, subject, collection


def find_best_format(files):
    """
    Find the best available text format.
    Priority: DjVuTXT > OCR Search Text > EPUB.
    Returns (format_name, filename) or (None, None).
    """
    priority = ["DjVuTXT", "OCR Search Text", "EPUB"]
    candidates = {}

    for f in files:
        fmt = f.get("format", "")
        name = f.get("name", "")
        if fmt in priority and name:
            if fmt not in candidates:
                candidates[fmt] = name

    for fmt in priority:
        if fmt in candidates:
            logger.info("[Format] 选择格式: %s → %s", fmt, candidates[fmt])
            return fmt, candidates[fmt]

    return None, None


# ── Text Download ──

def download_text(session, archive_id, filename):
    """Download text content from Archive.org. Works for DjVuTXT and OCR Search Text."""
    url = "https://archive.org/download/%s/%s" % (archive_id, filename)
    logger.info("[Download] 下载: %s", url)
    resp = session.get(url, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    text = resp.text
    logger.info("[Download] 下载完成，大小: %.1f KB", len(text) / 1024.0)
    return text


def download_epub_text(session, archive_id, filename):
    """
    Download EPUB and extract text using ebooklib.
    Returns extracted text string, or None if ebooklib is unavailable.
    """
    url = "https://archive.org/download/%s/%s" % (archive_id, filename)
    logger.info("[Download] 下载 EPUB: %s", url)
    resp = session.get(url, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    logger.info("[Download] EPUB 下载完成，大小: %.1f KB", len(resp.content) / 1024.0)

    try:
        import ebooklib
        from ebooklib import epub
    except ImportError:
        logger.warning("[EPUB] ebooklib 未安装，无法提取 EPUB 内容")
        return None

    try:
        book = epub.read_epub(io.BytesIO(resp.content))
        text_parts = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                try:
                    from bs4 import BeautifulSoup
                    html_text = BeautifulSoup(
                        item.get_content(), "html.parser"
                    ).get_text()
                    text_parts.append(html_text)
                except Exception:
                    # Non-HTML document type (e.g., XML), try raw
                    try:
                        text_parts.append(item.get_content().decode("utf-8"))
                    except Exception:
                        pass

        full_text = "\n\n".join(text_parts)
        logger.info("[EPUB] 提取完成，文本大小: %.1f KB", len(full_text) / 1024.0)
        return full_text
    except Exception as e:
        logger.error("[EPUB] 提取失败: %s", e)
        return None


# ── File I/O ──

def get_download_path(archive_id):
    """Return the local file path for a given archive_id."""
    return os.path.join(DOWNLOAD_DIR, "%s.txt" % archive_id)


def file_exists(file_path):
    """Check if the downloaded file already exists."""
    return os.path.isfile(file_path)


def write_text_file(file_path, text):
    """Write text to file, creating parent directories as needed."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info("[File] 写入文件: %s", file_path)


def count_lines(file_path):
    """Count number of lines in a text file."""
    count = 0
    with open(file_path, "r", encoding="utf-8") as f:
        for _ in f:
            count += 1
    return count


# ── Main ──

def main():
    args = parse_args()
    setup_logging(args.debug)

    session = make_session()

    # Step 1: Fetch metadata
    try:
        files, title, subject, collection = fetch_metadata(session, args.archive_id)
    except Exception as e:
        result = {
            "success": False,
            "error": "metadata_fetch_failed",
            "message": "无法获取元数据: %s" % str(e)
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # Step 2: Check if file already exists
    download_path = get_download_path(args.archive_id)
    if file_exists(download_path):
        file_size_bytes = os.path.getsize(download_path)
        file_size_kb = round(file_size_bytes / 1024.0, 1)
        file_size_lines = count_lines(download_path)
        logger.info("[Cache] 文件已存在，跳过下载: %s (%.1f KB, %d 行)",
                    download_path, file_size_kb, file_size_lines)

        result = {
            "success": True,
            "archive_id": args.archive_id,
            "title": title,
            "format_used": "cached",
            "file_path": download_path,
            "file_size_kb": file_size_kb,
            "file_size_lines": file_size_lines,
            "subject": subject,
            "collection": collection,
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    # Step 3: Find best format
    fmt_name, fmt_filename = find_best_format(files)
    if fmt_name is None:
        result = {
            "success": False,
            "error": "no_suitable_format",
            "message": (
                "未找到可用格式。需要 DjVuTXT / OCR Search Text / EPUB 之一。"
            )
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # Step 4: Download text
    try:
        if fmt_name == "EPUB":
            text = download_epub_text(session, args.archive_id, fmt_filename)
            if text is None:
                result = {
                    "success": False,
                    "error": "no_suitable_format",
                    "message": (
                        "只有 EPUB 格式可用，但 ebooklib 未安装。"
                        "请执行 pip install ebooklib 或使用其他 archive_id。"
                    )
                }
                print(json.dumps(result, ensure_ascii=False))
                sys.exit(1)
        else:
            text = download_text(session, args.archive_id, fmt_filename)
    except Exception as e:
        result = {
            "success": False,
            "error": "download_failed",
            "message": "下载失败: %s" % str(e)
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # Step 5: Write to file
    try:
        write_text_file(download_path, text)
    except Exception as e:
        result = {
            "success": False,
            "error": "file_write_failed",
            "message": "写入文件失败: %s" % str(e)
        }
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    # Step 6: Compute stats
    file_size_bytes = os.path.getsize(download_path)
    file_size_kb = round(file_size_bytes / 1024.0, 1)
    file_size_lines = count_lines(download_path)

    result = {
        "success": True,
        "archive_id": args.archive_id,
        "title": title,
        "format_used": fmt_name,
        "file_path": download_path,
        "file_size_kb": file_size_kb,
        "file_size_lines": file_size_lines,
        "subject": subject,
        "collection": collection,
    }

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
