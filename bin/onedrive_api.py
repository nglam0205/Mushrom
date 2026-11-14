"""
OneDrive Business API - Thay thế rclone
Microsoft Graph API direct access
"""

import time
import json
import logging
import os
import threading
import requests
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional, List, Dict
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ===================== CONFIG =====================
ONEDRIVE_CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID", "SHARE_REDACTED_VALUE")
ONEDRIVE_CLIENT_SECRET = (
    os.getenv("ONEDRIVE_CLIENT_SECRET")
    or "SHARE_REDACTED_VALUE"
)
ONEDRIVE_TENANT_ID = os.getenv("ONEDRIVE_TENANT_ID", "195eb391-67ab-44f9-b7b8-f9359db3c3c0")
ONEDRIVE_DRIVE_ID = os.getenv(
    "ONEDRIVE_DRIVE_ID",
    "b!zVgBm9UlnEuOFMvBKSIc0R5yIC0pCOlBmI2I4ES-ky9JWmLSeh0xT4dmFIzbMgki",
)
TOKEN_FILE = os.getenv(
    "ONEDRIVE_TOKEN_FILE",
    os.path.join(os.path.dirname(__file__), "onedrive_token.json"),
)
TOKEN_REFRESH_BUFFER = 300
REQUEST_MAX_RETRIES = 4
RETRY_MAX_DELAY = 60.0
UPLOAD_FRAGMENT_GRANULARITY = 320 * 1024
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
MAX_CHUNK_SIZE = 60 * 1024 * 1024 - UPLOAD_FRAGMENT_GRANULARITY
SIMPLE_UPLOAD_THRESHOLD = 10 * 1024 * 1024
# ==================================================

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = f"https://login.microsoftonline.com/{ONEDRIVE_TENANT_ID}/oauth2/v2.0/token"

# Token cache
_token_cache = {"token": None, "expires_at": 0}
_token_lock = threading.RLock()
_auto_refresh_thread = None
_auto_refresh_running = False

# Reuse session for connection pooling
_session = requests.Session()


def _response_error(response: requests.Response) -> str:
    """Return a concise Graph error without leaking auth headers or upload URLs."""
    request_id = response.headers.get("request-id") or response.headers.get("client-request-id")
    message = ""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "")
    except (ValueError, TypeError):
        message = response.text[:300]

    details = f"HTTP {response.status_code}"
    if message:
        details += f": {message}"
    if request_id:
        details += f" (request-id={request_id})"
    return details


def _retry_delay(response: Optional[requests.Response], attempt: int) -> float:
    """Honor Retry-After, otherwise use bounded exponential backoff."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(RETRY_MAX_DELAY, max(0.0, float(retry_after)))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return min(RETRY_MAX_DELAY, max(0.0, delay))
                except (TypeError, ValueError, OverflowError):
                    pass
    return min(RETRY_MAX_DELAY, float(2 ** attempt))


def _request(
    method: str,
    url: str,
    *,
    auth: bool = True,
    max_retries: int = REQUEST_MAX_RETRIES,
    **kwargs: Any,
) -> Optional[requests.Response]:
    """Run an HTTP request with token refresh, throttling and transient retries."""
    base_headers = dict(kwargs.pop("headers", {}) or {})
    refreshed_token = False

    for attempt in range(max_retries + 1):
        headers = dict(base_headers)
        if auth:
            token = _get_token()
            if not token:
                return None
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = _session.request(method, url, headers=headers, **kwargs)
        except requests.RequestException as exc:
            if attempt >= max_retries:
                logger.error("OneDrive request failed after %s attempt(s): %s", attempt + 1, exc)
                return None
            delay = _retry_delay(None, attempt)
            logger.warning("OneDrive request interrupted; retrying in %.1fs", delay)
            time.sleep(delay)
            continue

        if response.status_code == 401 and auth and not refreshed_token:
            refreshed_token = True
            with _token_lock:
                _token_cache["expires_at"] = 0
                if not _fetch_new_token():
                    return response
            continue

        if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
            delay = _retry_delay(response, attempt)
            logger.warning(
                "OneDrive returned HTTP %s; retrying in %.1fs",
                response.status_code,
                delay,
            )
            time.sleep(delay)
            continue

        return response

    return None


def _load_token_from_file() -> bool:
    """Load token từ file nếu có"""
    global _token_cache
    if os.path.exists(TOKEN_FILE):
        try:
            os.chmod(TOKEN_FILE, 0o600)
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("token") and data.get("expires_at", 0) > time.time():
                    _token_cache = data
                    logger.info(f"Loaded token from file, expires at {datetime.fromtimestamp(data['expires_at'])}")
                    return True
                else:
                    logger.info("Token file exists but expired, will fetch new token")
        except Exception as e:
            logger.warning(f"Failed to load token from file: {e}")
    return False


def _ensure_token_exists() -> bool:
    """
    Đảm bảo token tồn tại - tự động tạo nếu chưa có file
    
    Returns:
        True nếu token sẵn sàng sử dụng
    """
    global _token_cache
    
    # Thử load từ file trước
    if _load_token_from_file():
        return True
    
    # Nếu không có hoặc hết hạn, tự động fetch token mới
    logger.info("Token file not found or expired, fetching new token...")
    token = _fetch_new_token()
    
    if token:
        logger.info(f"Token auto-created and saved to {TOKEN_FILE}")
        return True
    else:
        logger.error("Failed to auto-create token")
        return False


def _save_token_to_file() -> bool:
    """Lưu token atomically với quyền chỉ dành cho owner."""
    temp_file = f"{TOKEN_FILE}.tmp"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(TOKEN_FILE)), exist_ok=True)
        fd = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_token_cache, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, TOKEN_FILE)
        os.chmod(TOKEN_FILE, 0o600)
        return True
    except Exception as e:
        logger.warning(f"Failed to save token to file: {e}")
        try:
            os.remove(temp_file)
        except OSError:
            pass
        return False


def _fetch_new_token() -> Optional[str]:
    """Lấy token mới từ Microsoft"""
    missing = [
        name
        for name, value in (
            ("ONEDRIVE_CLIENT_ID", ONEDRIVE_CLIENT_ID),
            ("ONEDRIVE_CLIENT_SECRET", ONEDRIVE_CLIENT_SECRET),
            ("ONEDRIVE_TENANT_ID", ONEDRIVE_TENANT_ID),
        )
        if not value
    ]
    if missing:
        logger.error("Missing OneDrive configuration: %s", ", ".join(missing))
        return None

    try:
        r = _session.post(TOKEN_URL, data={
            "client_id": ONEDRIVE_CLIENT_ID,
            "client_secret": ONEDRIVE_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials"
        }, timeout=10)
        if not r.ok:
            logger.error("OneDrive token error: %s", _response_error(r))
            return None
        
        data = r.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        _save_token_to_file()
        
        logger.info(f"New token obtained, expires at {datetime.fromtimestamp(_token_cache['expires_at'])}")
        return _token_cache["token"]
    except (requests.RequestException, ValueError, KeyError) as e:
        logger.error(f"OneDrive token error: {e}")
        return None


def _get_token() -> Optional[str]:
    """Get access token with caching"""
    with _token_lock:
        # Thử load từ file nếu cache trống
        if not _token_cache["token"]:
            _load_token_from_file()
        
        # Trả về token nếu còn hạn
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
        
        # Lấy token mới
        return _fetch_new_token()


def init_token() -> bool:
    """
    Khởi tạo token - gọi khi startup
    Tự động tạo token file nếu chưa có
    
    Returns:
        True nếu token sẵn sàng
    """
    with _token_lock:
        return _ensure_token_exists()


# ============ TOKEN MANAGEMENT FUNCTIONS ============

def check_token_status(auto_init: bool = False) -> Dict:
    """
    Kiểm tra trạng thái token hiện tại
    
    Args:
        auto_init: Tự động tạo token nếu chưa có (default: False)
    
    Returns:
        Dict với thông tin: valid, expires_at, remaining_seconds, remaining_human
    """
    with _token_lock:
        if not _token_cache["token"]:
            _load_token_from_file()
        
        if not _token_cache["token"]:
            # Auto-init nếu được yêu cầu
            if auto_init:
                logger.info("Auto-initializing token...")
                if _ensure_token_exists():
                    # Token đã được tạo, gọi lại check_token_status
                    pass  # Continue to check status below
                else:
                    return {
                        "valid": False,
                        "expires_at": None,
                        "remaining_seconds": 0,
                        "remaining_human": "Failed to create token",
                        "status": "INIT_FAILED"
                    }
            else:
                return {
                    "valid": False,
                    "expires_at": None,
                    "remaining_seconds": 0,
                    "remaining_human": "No token",
                    "status": "NO_TOKEN"
                }
        
        remaining = _token_cache["expires_at"] - time.time()
        
        if remaining <= 0:
            return {
                "valid": False,
                "expires_at": datetime.fromtimestamp(_token_cache["expires_at"]).isoformat(),
                "remaining_seconds": 0,
                "remaining_human": "Expired",
                "status": "EXPIRED"
            }
        
        # Format remaining time
        mins, secs = divmod(int(remaining), 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            remaining_human = f"{hours}h {mins}m {secs}s"
        elif mins > 0:
            remaining_human = f"{mins}m {secs}s"
        else:
            remaining_human = f"{secs}s"
        
        status = "VALID" if remaining > TOKEN_REFRESH_BUFFER else "EXPIRING_SOON"
        
        return {
            "valid": True,
            "expires_at": datetime.fromtimestamp(_token_cache["expires_at"]).isoformat(),
            "remaining_seconds": int(remaining),
            "remaining_human": remaining_human,
            "status": status
        }


def refresh_token(force: bool = False) -> Dict:
    """
    Refresh token
    
    Args:
        force: True = bắt buộc refresh, False = chỉ refresh nếu sắp hết hạn
        
    Returns:
        Dict với kết quả refresh
    """
    with _token_lock:
        status = check_token_status()
        
        if not force and status["valid"] and status["remaining_seconds"] > TOKEN_REFRESH_BUFFER:
            return {
                "refreshed": False,
                "reason": "Token still valid",
                "status": status
            }
        
        # Fetch new token
        new_token = _fetch_new_token()
        
        if new_token:
            return {
                "refreshed": True,
                "reason": "Token refreshed successfully",
                "status": check_token_status()
            }
        else:
            return {
                "refreshed": False,
                "reason": "Failed to refresh token",
                "status": check_token_status()
            }


def _auto_refresh_loop():
    """Background loop để tự động refresh token"""
    global _auto_refresh_running
    while _auto_refresh_running:
        try:
            status = check_token_status()
            
            # Refresh nếu token sắp hết hạn (còn < 5 phút)
            if status["status"] in ("EXPIRED", "EXPIRING_SOON", "NO_TOKEN"):
                logger.info("Auto-refreshing token...")
                refresh_token(force=True)
            
            # Sleep 1 phút rồi check lại
            for _ in range(60):
                if not _auto_refresh_running:
                    break
                time.sleep(1)
        except Exception as e:
            logger.error(f"Auto-refresh error: {e}")
            time.sleep(60)


def start_auto_refresh():
    """Bắt đầu auto-refresh token trong background"""
    global _auto_refresh_thread, _auto_refresh_running
    
    if _auto_refresh_running:
        return {"status": "Already running"}
    
    _auto_refresh_running = True
    _auto_refresh_thread = threading.Thread(target=_auto_refresh_loop, daemon=True)
    _auto_refresh_thread.start()
    
    logger.info("Auto-refresh started")
    return {"status": "Started", "check_interval": "60 seconds"}


def stop_auto_refresh():
    """Dừng auto-refresh"""
    global _auto_refresh_running
    _auto_refresh_running = False
    logger.info("Auto-refresh stopped")
    return {"status": "Stopped"}


def _headers() -> dict:
    """Get auth headers"""
    token = _get_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _parse_path(path: str) -> str:
    """
    Convert OneDrive:/PATH to API path
    e.g., OneDrive:/PAID/STABLE/CPH2585 -> PAID/STABLE/CPH2585
    """
    if not isinstance(path, str):
        return ""
    if path.startswith("OneDrive:"):
        path = path[9:]
    return path.strip("/")


def _encoded_path(path: str) -> str:
    """Encode each path component while keeping OneDrive folder separators."""
    return quote(_parse_path(path), safe="/")


def _item_url(path: str, operation: Optional[str] = None) -> str:
    encoded = _encoded_path(path)
    base = f"{GRAPH_BASE}/drives/{ONEDRIVE_DRIVE_ID}/root"
    if not encoded:
        return f"{base}/{operation}" if operation else base
    if operation:
        return f"{base}:/{encoded}:/{operation}"
    return f"{base}:/{encoded}"


def _item_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Name": item.get("name"),
        "Size": item.get("size", 0),
        "ModTime": item.get("lastModifiedDateTime"),
        "IsDir": "folder" in item,
        "ID": item.get("id"),
        "WebUrl": item.get("webUrl"),
    }


def list_files(path: str, files_only: bool = True) -> List[Dict]:
    """
    List files in folder (replaces rclone lsjson)
    
    Args:
        path: OneDrive:/FOLDER/SUBFOLDER or /FOLDER/SUBFOLDER
        files_only: Only return files, not folders
        
    Returns:
        List of file info dicts with Name, Size, ModTime, IsDir
    """
    api_path = _parse_path(path)
    
    # Build URL
    if not api_path:
        url = _item_url("", "children")
    else:
        url = _item_url(api_path, "children")
    
    try:
        result = []
        while url:
            r = _request("GET", url, timeout=30)
            if r is None or r.status_code == 404:
                return result
            if not r.ok:
                logger.error("OneDrive list error: %s", _response_error(r))
                return result

            payload = r.json()
            for item in payload.get("value", []):
                metadata = _item_metadata(item)
                if files_only and metadata["IsDir"]:
                    continue
                result.append(metadata)
            url = payload.get("@odata.nextLink")
        
        return result
    except (requests.RequestException, ValueError, TypeError) as e:
        logger.error(f"OneDrive list error: {e}")
        return []


def list_file_names(path: str) -> List[str]:
    """List file names only (convenience function)"""
    return [f["Name"] for f in list_files(path, files_only=True)]


def ensure_folder(path: str) -> bool:
    """Create missing folder components and reject paths occupied by files."""
    api_path = _parse_path(path)
    if not api_path:
        return True

    current_path = ""
    for component in api_path.split("/"):
        if not component or component in (".", ".."):
            logger.error("Invalid OneDrive folder component: %r", component)
            return False

        next_path = "/".join(part for part in (current_path, component) if part)
        existing = _request(
            "GET",
            _item_url(next_path),
            params={"$select": "id,name,folder"},
            timeout=30,
        )
        if existing is None:
            return False
        if existing.status_code == 200:
            try:
                if "folder" not in existing.json():
                    logger.error("OneDrive path is not a folder: %s", next_path)
                    return False
            except (ValueError, TypeError):
                logger.error("Invalid metadata while checking folder: %s", next_path)
                return False
            current_path = next_path
            continue
        if existing.status_code != 404:
            logger.error("Cannot inspect OneDrive folder %s: %s", next_path, _response_error(existing))
            return False

        parent_children_url = _item_url(current_path, "children")
        created = _request(
            "POST",
            parent_children_url,
            headers={"Content-Type": "application/json"},
            json={
                "name": component,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
            timeout=30,
        )
        if created is None:
            return False
        if created.status_code == 201:
            logger.info("Created OneDrive folder: %s", next_path)
            current_path = next_path
            continue
        if created.status_code == 409:
            raced = _request(
                "GET",
                _item_url(next_path),
                params={"$select": "id,folder"},
                timeout=30,
            )
            if raced is not None and raced.status_code == 200:
                try:
                    if "folder" in raced.json():
                        current_path = next_path
                        continue
                except (ValueError, TypeError):
                    pass

        logger.error("Cannot create OneDrive folder %s: %s", next_path, _response_error(created))
        return False

    return True


def generate_link(path: str, expire_hours: int = 5, direct: bool = True) -> Optional[str]:
    """
    Generate download link (replaces rclone link)
    
    Args:
        path: OneDrive:/FOLDER/file.zip
        expire_hours: Link expiration (ignored, uses anonymous link)
        direct: True = direct download URL, False = web preview URL
        
    Returns:
        Direct download URL or None
    """
    api_path = _parse_path(path)
    if not api_path:
        return None
    
    # Method 1: Get @microsoft.graph.downloadUrl from file metadata
    try:
        meta_url = _item_url(api_path)
        r = _request("GET", meta_url, timeout=30)
        
        if r is not None and r.status_code == 200:
            data = r.json()
            download_url = data.get("@microsoft.graph.downloadUrl")
            if download_url:
                logger.debug(f"Got direct download URL from metadata")
                return download_url
    except Exception as e:
        logger.debug(f"Metadata method failed: {e}")
    
    # Method 2: Get redirect from /content endpoint
    try:
        content_url = _item_url(api_path, "content")
        r = _request("HEAD", content_url, allow_redirects=False, timeout=30)
        
        if r is not None and r.status_code in (302, 307):
            location = r.headers.get("Location")
            if location:
                logger.debug(f"Got direct download URL from content redirect")
                return location
    except Exception as e:
        logger.debug(f"Content redirect method failed: {e}")
    
    # Method 3: Fallback - create sharing link and convert to direct download
    return _create_sharing_link(api_path, direct=direct)


def _create_sharing_link(api_path: str, direct: bool = True) -> Optional[str]:
    """
    Create anonymous sharing link as fallback
    
    Args:
        api_path: Path to file
        direct: True = convert to direct download URL
    """
    url = _item_url(api_path, "createLink")
    
    try:
        r = _request("POST", url, headers={
            "Content-Type": "application/json"
        }, json={
            "type": "view",
            "scope": "anonymous"
        }, timeout=30)
        if r is None or not r.ok:
            if r is not None:
                logger.error("Create sharing link error: %s", _response_error(r))
            return None
        
        web_url = r.json().get("link", {}).get("webUrl")
        
        if not web_url:
            return None
        
        if not direct:
            return web_url
        
        # Convert to direct download URL
        # https://xxx.sharepoint.com/:u:/g/personal/xxx/XXXX 
        # -> https://xxx.sharepoint.com/:u:/g/personal/xxx/XXXX?download=1
        if "sharepoint.com" in web_url:
            if "?" in web_url:
                return f"{web_url}&download=1"
            else:
                return f"{web_url}?download=1"
        
        return web_url
    except Exception as e:
        logger.error(f"Create sharing link error: {e}")
        return None


def delete_file(path: str) -> bool:
    """
    Delete file (replaces rclone delete)
    
    Args:
        path: OneDrive:/FOLDER/file.zip
        
    Returns:
        True if deleted successfully
    """
    api_path = _parse_path(path)
    if not api_path:
        return False
    
    url = _item_url(api_path)
    
    try:
        r = _request("DELETE", url, timeout=30)
        if r is None:
            return False
        if r.status_code in (200, 204, 404):
            return True
        logger.error("OneDrive delete error: %s", _response_error(r))
        return False
    except requests.RequestException as e:
        logger.error(f"OneDrive delete error: {e}")
        return False


def upload_file(
    local_path: str,
    remote_path: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    verify: bool = True,
    create_parents: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[Dict]:
    """
    Upload file to OneDrive
    
    Args:
        local_path: Path to local file
        remote_path: OneDrive:/FOLDER/filename.ext
        chunk_size: Chunk size for large files; must be a multiple of 320 KiB
        verify: Read metadata after upload and verify name/size
        create_parents: Create missing remote parent folders
        progress_callback: Optional callback receiving uploaded bytes and total bytes
        
    Returns:
        File metadata dict or None if failed
    """
    if not os.path.isfile(local_path):
        logger.error(f"Local file not found: {local_path}")
        return None

    if not ONEDRIVE_DRIVE_ID:
        logger.error("Missing OneDrive configuration: ONEDRIVE_DRIVE_ID")
        return None

    folder_hint = remote_path.rstrip().endswith("/")
    api_path = _parse_path(remote_path)
    if folder_hint or not api_path:
        api_path = "/".join(part for part in (api_path, os.path.basename(local_path)) if part)
    if not api_path:
        logger.error("Remote path is empty")
        return None

    try:
        chunk_size = int(chunk_size)
    except (TypeError, ValueError):
        logger.error("Invalid chunk size: %r", chunk_size)
        return None
    if (
        chunk_size < UPLOAD_FRAGMENT_GRANULARITY
        or chunk_size > MAX_CHUNK_SIZE
        or chunk_size % UPLOAD_FRAGMENT_GRANULARITY != 0
    ):
        logger.error(
            "Chunk size must be a multiple of 320 KiB and smaller than 60 MiB: %s",
            chunk_size,
        )
        return None

    parent_path = api_path.rpartition("/")[0]
    if create_parents and parent_path and not ensure_folder(parent_path):
        return None

    file_size = os.path.getsize(local_path)

    if file_size <= SIMPLE_UPLOAD_THRESHOLD:
        result = _upload_simple(local_path, api_path)
        if result and progress_callback:
            progress_callback(file_size, file_size)
    else:
        result = _upload_large(
            local_path,
            api_path,
            file_size,
            chunk_size,
            progress_callback=progress_callback,
        )

    if not result or not verify:
        return result

    remote_info = get_file_info(api_path)
    expected_name = os.path.basename(api_path)
    if (
        not remote_info
        or remote_info.get("Name") != expected_name
        or remote_info.get("Size") != file_size
    ):
        logger.error(
            "Upload verification failed for %s (expected name=%s size=%s)",
            api_path,
            expected_name,
            file_size,
        )
        return None

    result.update(remote_info)
    result["Verified"] = True
    return result


def _upload_simple(local_path: str, api_path: str) -> Optional[Dict]:
    """Upload a small file in one request."""
    url = _item_url(api_path, "content")
    
    try:
        with open(local_path, 'rb') as f:
            data = f.read()
        
        r = _request("PUT", url, headers={
            "Content-Type": "application/octet-stream"
        }, data=data, timeout=60)

        if r is not None and r.status_code in (200, 201):
            item = r.json()
            logger.info(f"Uploaded (simple): {api_path}")
            return _item_metadata(item)
        if r is not None:
            logger.error("Upload failed: %s", _response_error(r))
        return None
    except (OSError, ValueError, requests.RequestException) as e:
        logger.error(f"Simple upload error: {e}")
        return None


def _create_upload_session(api_path: str) -> Optional[str]:
    session_url = _item_url(api_path, "createUploadSession")
    r = _request(
        "POST",
        session_url,
        headers={"Content-Type": "application/json"},
        json={
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": os.path.basename(api_path),
            }
        },
        timeout=30,
    )
    if r is None:
        return None
    if r.status_code not in (200, 201):
        logger.error("Failed to create upload session: %s", _response_error(r))
        return None

    try:
        upload_url = r.json().get("uploadUrl")
    except (ValueError, AttributeError):
        upload_url = None
    if not upload_url:
        logger.error("No upload URL in response")
        return None
    return upload_url


def _next_expected_offset(response: requests.Response) -> Optional[int]:
    try:
        ranges = response.json().get("nextExpectedRanges", [])
        if not ranges:
            return None
        return int(str(ranges[0]).split("-", 1)[0])
    except (ValueError, TypeError, AttributeError, IndexError):
        return None


def _cancel_upload_session(upload_url: str) -> None:
    try:
        _request("DELETE", upload_url, auth=False, max_retries=0, timeout=15)
    except Exception:
        pass


def _upload_large(
    local_path: str,
    api_path: str,
    file_size: int,
    chunk_size: int,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    session_restarts: int = 1,
) -> Optional[Dict]:
    """
    Resumable upload for files larger than the simple-upload threshold.
    Uses upload session with chunked transfer
    """
    upload_url = _create_upload_session(api_path)
    if not upload_url:
        return None

    logger.info(f"Upload session created for {api_path} ({file_size / (1024*1024):.1f} MiB)")

    try:
        with open(local_path, 'rb') as f:
            uploaded = 0

            while uploaded < file_size:
                chunk_start = uploaded
                f.seek(chunk_start)
                chunk_data = f.read(min(chunk_size, file_size - chunk_start))
                actual_chunk_size = len(chunk_data)
                if not actual_chunk_size:
                    logger.error("Unexpected end of local file at byte %s", chunk_start)
                    _cancel_upload_session(upload_url)
                    return None
                chunk_end = chunk_start + actual_chunk_size - 1

                headers = {
                    "Content-Length": str(actual_chunk_size),
                    "Content-Range": f"bytes {chunk_start}-{chunk_end}/{file_size}"
                }

                r = _request(
                    "PUT",
                    upload_url,
                    auth=False,
                    headers=headers,
                    data=chunk_data,
                    timeout=300,
                )
                if r is None:
                    _cancel_upload_session(upload_url)
                    return None

                if r.status_code == 202:
                    next_offset = _next_expected_offset(r)
                    uploaded = next_offset if next_offset is not None else chunk_end + 1
                    if not 0 <= uploaded <= file_size:
                        logger.error("Invalid nextExpectedRanges offset: %s", uploaded)
                        _cancel_upload_session(upload_url)
                        return None
                    if progress_callback:
                        progress_callback(uploaded, file_size)

                elif r.status_code in (200, 201):
                    item = r.json()
                    logger.info(f"Uploaded (large): {api_path}")
                    if progress_callback:
                        progress_callback(file_size, file_size)
                    return _item_metadata(item)
                elif r.status_code == 416:
                    status = _request("GET", upload_url, auth=False, timeout=30)
                    next_offset = _next_expected_offset(status) if status is not None else None
                    if next_offset is None:
                        logger.error("Cannot resume upload after HTTP 416")
                        _cancel_upload_session(upload_url)
                        return None
                    uploaded = next_offset
                elif r.status_code == 404:
                    if session_restarts > 0:
                        logger.warning("Upload session expired; creating a new session")
                        return _upload_large(
                            local_path,
                            api_path,
                            file_size,
                            chunk_size,
                            progress_callback=progress_callback,
                            session_restarts=session_restarts - 1,
                        )
                    logger.error("Upload session expired after restart")
                    return None
                else:
                    logger.error("Chunk upload failed: %s", _response_error(r))
                    _cancel_upload_session(upload_url)
                    return None

        return None

    except (OSError, ValueError, requests.RequestException) as e:
        logger.error(f"Large upload error: {e}")
        _cancel_upload_session(upload_url)
        return None


def file_exists(path: str) -> bool:
    """Check if file exists"""
    api_path = _parse_path(path)
    if not api_path:
        return False
    
    url = _item_url(api_path)
    
    try:
        # Use GET with $select to minimize data transfer (HEAD doesn't always work)
        r = _request("GET", url, params={"$select": "id"}, timeout=10)
        return r is not None and r.status_code == 200
    except requests.RequestException as exc:
        logger.error("OneDrive exists error: %s", exc)
        return False


def get_file_info(path: str) -> Optional[Dict]:
    """Get file metadata"""
    api_path = _parse_path(path)
    if not api_path:
        return None
    
    url = _item_url(api_path)
    
    try:
        r = _request("GET", url, timeout=10)
        if r is None or r.status_code != 200:
            return None
        return _item_metadata(r.json())
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.error("OneDrive metadata error: %s", exc)
        return None


# ============ RCLONE-COMPATIBLE WRAPPERS ============

def lsjson(path: str, files_only: bool = True) -> str:
    """
    rclone lsjson compatible - returns JSON string
    """
    files = list_files(path, files_only=files_only)
    return json.dumps(files)


def link(path: str, expire_hours: int = 5) -> Optional[str]:
    """rclone link compatible"""
    return generate_link(path, expire_hours)


def delete(path: str) -> bool:
    """rclone delete compatible"""
    return delete_file(path)


# ============ AUTO-INITIALIZATION ============

def _auto_init_on_import():
    """
    Tự động khởi tạo token khi module được import
    Chạy trong background để không block
    """
    def init_worker():
        try:
            if not os.path.exists(TOKEN_FILE):
                logger.info("Token file not found, auto-creating...")
                if init_token():
                    logger.info("✅ Token auto-initialized successfully")
                else:
                    logger.warning("⚠️ Failed to auto-initialize token")
            else:
                # Load existing token to cache
                _load_token_from_file()
        except Exception as e:
            logger.error(f"Auto-init error: {e}")
    
    # Run in background thread to avoid blocking import
    init_thread = threading.Thread(target=init_worker, daemon=True)
    init_thread.start()
    # Wait briefly for init to complete (max 2 seconds)
    init_thread.join(timeout=2)


# ============ COMMAND LINE INTERFACE ============

def _remote_arg(path: str) -> str:
    return path if path.startswith("OneDrive:") else f"OneDrive:{path}"


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Upload and manage files with Microsoft Graph/OneDrive")
    parser.add_argument("--verbose", action="store_true", help="enable verbose logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload_parser = subparsers.add_parser("upload", help="upload or replace a file")
    upload_parser.add_argument("local_file")
    upload_parser.add_argument("remote_path", help="full file path, or a folder path ending in /")
    upload_parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="chunk size in bytes")
    upload_parser.add_argument("--no-verify", action="store_true", help="skip post-upload metadata verification")
    upload_parser.add_argument("--no-create-parents", action="store_true", help="require parent folders to exist")
    upload_parser.add_argument("--json", action="store_true", help="print result metadata as JSON")

    link_parser = subparsers.add_parser("link", help="generate a download link")
    link_parser.add_argument("remote_path")

    list_parser = subparsers.add_parser("list", help="list a folder")
    list_parser.add_argument("remote_path")
    list_parser.add_argument("--all", action="store_true", help="include folders")
    list_parser.add_argument("--json", action="store_true", help="print JSON metadata")

    exists_parser = subparsers.add_parser("exists", help="check whether an item exists")
    exists_parser.add_argument("remote_path")

    info_parser = subparsers.add_parser("info", help="get item metadata")
    info_parser.add_argument("remote_path")

    delete_parser = subparsers.add_parser("delete", help="delete an item")
    delete_parser.add_argument("remote_path")

    subparsers.add_parser("token-status", help="show cached token status")
    refresh_parser = subparsers.add_parser("refresh-token", help="fetch a new app token")
    refresh_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.command == "upload":
        last_percent = -1

        def show_progress(uploaded: int, total: int) -> None:
            nonlocal last_percent
            percent = int(uploaded * 100 / total) if total else 100
            if percent != last_percent:
                print(f"Upload progress: {percent}%", file=sys.stderr)
                last_percent = percent

        result = upload_file(
            args.local_file,
            _remote_arg(args.remote_path),
            chunk_size=args.chunk_size,
            verify=not args.no_verify,
            create_parents=not args.no_create_parents,
            progress_callback=show_progress,
        )
        if not result:
            print("Upload failed", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            verified = " (verified)" if result.get("Verified") else ""
            print(f"Upload successful: {result.get('Name')}{verified}")
        return 0

    if args.command == "link":
        link_url = generate_link(_remote_arg(args.remote_path))
        if not link_url:
            print("Failed to generate link", file=sys.stderr)
            return 1
        print(link_url)
        return 0

    if args.command == "list":
        files = list_files(_remote_arg(args.remote_path), files_only=not args.all)
        if args.json:
            print(json.dumps(files, ensure_ascii=False))
        else:
            for item in files:
                print(item["Name"])
        return 0

    if args.command == "exists":
        return 0 if file_exists(_remote_arg(args.remote_path)) else 1

    if args.command == "info":
        info = get_file_info(_remote_arg(args.remote_path))
        if not info:
            return 1
        print(json.dumps(info, ensure_ascii=False))
        return 0

    if args.command == "delete":
        if not delete_file(_remote_arg(args.remote_path)):
            print("Delete failed", file=sys.stderr)
            return 1
        print("Delete successful")
        return 0

    if args.command == "token-status":
        print(json.dumps(check_token_status(), ensure_ascii=False))
        return 0

    if args.command == "refresh-token":
        result = refresh_token(force=args.force)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("refreshed") or result.get("reason") == "Token still valid" else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
