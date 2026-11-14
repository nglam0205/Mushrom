#!/usr/bin/env python3
"""Query OPlus OTA servers and resolve the newest ColorOS full-ROM URL."""

import argparse
import base64
import json
import os
import random
import re
import shlex
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


# OTA protocol and public server configuration integrated from OPlus-Tracker:
# https://github.com/JerryTse-OSS/OPlus-Tracker
OTA_PUBLIC_KEYS = {
    "cn": """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApXYGXQpNL7gmMzzvajHa
oZIHQQvBc2cOEhJc7/tsaO4sT0unoQnwQKfNQCuv7qC1Nu32eCLuewe9LSYhDXr9
KSBWjOcCFXVXteLO9WCaAh5hwnUoP/5/Wz0jJwBA+yqs3AaGLA9wJ0+B2lB1vLE4
FZNE7exUfwUc03fJxHG9nCLKjIZlrnAAHjRCd8mpnADwfkCEIPIGhnwq7pdkbamZ
coZfZud1+fPsELviB9u447C6bKnTU4AaMcR9Y2/uI6TJUTcgyCp+ilgU0JxemrSI
PFk3jbCbzamQ6Shkw/jDRzYoXpBRg/2QDkbq+j3ljInu0RHDfOeXf3VBfHSnQ66H
CwIDAQAB
-----END RSA PUBLIC KEY-----""",
    "eu": """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAh8/EThsK3f0WyyPgrtXb
/D0Xni6UZNppaQHUqHWo976cybl92VxmehE0ISObnxERaOtrlYmTPIxkVC9MMueD
vTwZ1l0KxevZVKU0sJRxNR9AFcw6D7k9fPzzpNJmhSlhpNbt3BEepdgibdRZbacF
3NWy3ejOYWHgxC+I/Vj1v7QU5gD+1OhgWeRDcwuV4nGY1ln2lvkRj8EiJYXfkSq/
wUI5AvPdNXdEqwou4FBcf6mD84G8pKDyNTQwwuk9lvFlcq4mRqgYaFg9DAgpDgqV
K4NTJWM7tQS1GZuRA6PhupfDqnQExyBFhzCefHkEhcFywNyxlPe953NWLFWwbGvF
KwIDAQAB
-----END RSA PUBLIC KEY-----""",
    "in": """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAwYtghkzeStC9YvAwOQmW
ylbp74Tj8hhi3f9IlK7A/CWrGbLgzz/BeKxNb45zBN8pgaaEOwAJ1qZQV5G4nPro
WCPOP1ro1PkemFJvw/vzOOT5uN0ADnHDzZkZXCU/knxqUSfLcwQlHXsYhNsAm7uO
KjY9YXF4zWzYN0eFPkML3Pj/zg7hl/ov9clB2VeyI1/blMHFfcNA/fvqDTENXcNB
IhgJvXiCpLcZqp+aLZPC5AwY/sCb3j5jTWer0Rk0ZjQBZE1AncwYvUx4mA65U59c
WpTyl4c47J29MsQ66hqWv6eBHlDNZSEsQpHePUqgsf7lmO5Wd7teB8ugQki2oz1Y
5QIDAQAB
-----END RSA PUBLIC KEY-----""",
    "sg": """-----BEGIN RSA PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAkA980wxi+eTGcFDiw2I6
RrUeO4jL/Aj3Yw4dNuW7tYt+O1sRTHgrzxPD9SrOqzz7G0KgoSfdFHe3JVLPN+U1
waK+T0HfLusVJshDaMrMiQFDUiKajb+QKr+bXQhVofH74fjat+oRJ8vjXARSpFk4
/41x5j1Bt/2bHoqtdGPcUizZ4whMwzap+hzVlZgs7BNfepo24PWPRujsN3uopl+8
u4HFpQDlQl7GdqDYDj2zNOHdFQI2UpSf0aIeKCKOpSKF72KDEESpJVQsqO4nxMwE
i2jMujQeCHyTCjBZ+W35RzwT9+0pyZv8FB3c7FYY9FdF/+lvfax5mvFEBd9jO+dp
MQIDAQAB
-----END RSA PUBLIC KEY-----""",
}

OTA_REGION_CONFIG = {
    "cn": ("component-ota-cn.allawntech.com", "zh-CN", "10010111", "cn", "1615879139745"),
    "cn_cmcc": ("component-ota-cn.allawntech.com", "zh-CN", "10011000", "cn", "1615879139745"),
    "eu": ("component-ota-eu.allawnos.com", "en-GB", "01000100", "eu", "1615897067573"),
    "in": ("component-ota-in.allawnos.com", "en-IN", "00011011", "in", "1615896309308"),
    "sg": ("component-ota-sg.allawnos.com", "en-SG", "01011010", "sg", "1615895993238"),
    "gl": ("component-ota-sg.allawnos.com", "en-US", "10100111", "sg", "1615895993238"),
    "id": ("component-ota-sg.allawnos.com", "id-ID", "00110011", "sg", "1615895993238"),
    "ru": ("component-ota-sg.allawnos.com", "ru-RU", "00110111", "sg", "1615895993238"),
    "th": ("component-ota-sg.allawnos.com", "th-TH", "00111001", "sg", "1615895993238"),
    "tw": ("component-ota-sg.allawnos.com", "zh-TW", "00011010", "sg", "1615895993238"),
    "my": ("component-ota-sg.allawnos.com", "ms-MY", "00111000", "sg", "1615895993238"),
    "vn": ("component-ota-sg.allawnos.com", "vi-VN", "00111100", "sg", "1615895993238"),
    "sa": ("component-ota-sg.allawnos.com", "sa-SA", "10000011", "sg", "1615895993238"),
    "mea": ("component-ota-sg.allawnos.com", "en-MEA", "10100110", "sg", "1615895993238"),
    "ph": ("component-ota-sg.allawnos.com", "en-PH", "00111110", "sg", "1615895993238"),
    "roe": ("component-ota-sg.allawnos.com", "en-EU", "10001101", "sg", "1615895993238"),
    "la": ("component-ota-sg.allawnos.com", "en-LA", "10011010", "sg", "1615895993238"),
    "tr": ("component-ota-sg.allawnos.com", "tr-TR", "01010001", "sg", "1615895993238"),
    "br": ("component-ota-sg.allawnos.com", "en-BR", "10011110", "sg", "1615895993238"),
    "jp": ("component-ota-sg.allawnos.com", "ja-JP", "00111011", "sg", "1615895993238"),
    "hk": ("component-ota-sg.allawnos.com", "zh-HK", "10000010", "sg", "1615895993238"),
    "au": ("component-ota-sg.allawnos.com", "en-AU", "00011110", "sg", "1615895993238"),
    "lk": ("component-ota-sg.allawnos.com", "en-LK", "00011011", "sg", "1615895993238"),
    "eea": ("component-ota-sg.allawnos.com", "en-EU", "01000100", "sg", "1615895993238"),
    "kz": ("component-ota-sg.allawnos.com", "ru-KZ", "01011010", "sg", "1615895993238"),
    "mx": ("component-ota-sg.allawnos.com", "es-MX", "01111011", "sg", "1615895993238"),
    "eg": ("component-ota-sg.allawnos.com", "ar-EG", "01110101", "sg", "1615895993238"),
    "8d": ("component-ota-sg.allawnos.com", "en-EU", "10001101", "sg", "1615895993238"),
    "latam": ("component-ota-sg.allawnos.com", "en-LA", "10011010", "sg", "1615895993238"),
    "ex": ("component-ota-sg.allawnos.com", "en-US", "00000000", "sg", "1615895993238"),
    "apc": ("component-ota-sg.allawnos.com", "en-US", "10100100", "sg", "1615895993238"),
    "oca": ("component-ota-sg.allawnos.com", "en-AU", "10100101", "sg", "1615895993238"),
}

PUBLIC_OTA_REGIONS = tuple(OTA_REGION_CONFIG)

ANDROID_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; SM-G998B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/112.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "userId": "oplus-ota|00000001",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(source):
    if source.startswith(("http://", "https://", "file://")):
        request = urllib.request.Request(source, headers=ANDROID_HEADERS)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    with open(source, "r", encoding="utf-8") as stream:
        return json.load(stream)


def resolve_device_code(data, requested_code):
    code = requested_code.strip().upper()
    if code in data:
        return code, ""

    # Build-tool aliases such as CPH2649IN select the CPH2649 dataset and
    # retain IN as the preferred OTA region.
    matches = []
    for candidate in data:
        candidate_upper = str(candidate).upper()
        suffix = code[len(candidate_upper) :]
        if code.startswith(candidate_upper) and suffix in {
            "APC", "BR", "CN", "EEA", "EU", "EX", "ID", "IN", "MEA",
            "MX", "MY", "OCA", "SA", "TH", "TR", "TW", "VN",
        }:
            matches.append((len(candidate_upper), candidate_upper, suffix))
    if matches:
        _, data_code, region = max(matches)
        return data_code, region
    raise ValueError(f"No ColorOS OTA data found for device: {code}")


def version_numbers(value):
    text = str(value or "").strip()
    tail = text.split("_", 1)[-1]
    tail = re.sub(r"\([^)]+\)\s*$", "", tail).strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", tail):
        return tuple(int(part) for part in tail.split("."))
    matches = re.findall(r"\d+(?:\.\d+)+", text)
    return tuple(int(part) for part in matches[-1].split(".")) if matches else ()


def select_ota(data, requested_code, requested_region=""):
    data_code, alias_region = resolve_device_code(data, requested_code)
    items = data.get(data_code)
    if not isinstance(items, list):
        raise ValueError(f"Invalid ColorOS OTA list for device: {data_code}")

    candidates = [
        item
        for item in items
        if isinstance(item, dict)
        and not bool(item.get("is_gray"))
        and str(item.get("download_check_link") or "").startswith(("http://", "https://"))
    ]
    if not candidates:
        raise ValueError(f"No official ColorOS OTA download found for device: {data_code}")

    region = (requested_region or alias_region).strip().upper()
    if region:
        region_candidates = [
            item for item in candidates
            if str(item.get("region_code") or "").strip().upper() == region
        ]
        if region_candidates:
            candidates = region_candidates
        elif requested_region:
            raise ValueError(f"No official ColorOS OTA found for {data_code} region {region}")

    candidates.sort(
        key=lambda item: (
            version_numbers(item.get("version_name")),
            str(item.get("published_time") or ""),
            str(item.get("security_os") or ""),
        ),
        reverse=True,
    )
    return data_code, candidates[0]


def tracker_value(block, label):
    match = re.search(
        rf"^[• ]*{re.escape(label)}:\s*(.+?)\s*$",
        block,
        flags=re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def select_tracker_ota(output, requested_code, requested_region=""):
    """Parse OPlus-Tracker output and select the highest full-ROM version."""
    candidates = []
    for block in re.split(r"(?=^Fetch Info:\s*$)", output, flags=re.MULTILINE):
        if not block.startswith("Fetch Info:"):
            continue
        link = tracker_value(block, "Link")
        version = tracker_value(block, "Version")
        ota_version = tracker_value(block, "Ota Version")
        if not link.startswith(("http://", "https://")) or not version:
            continue
        candidates.append(
            {
                "download_check_link": tracker_value(block, "Original Link") or link,
                "resolved_link": link,
                "version_name": version,
                "ota_version": ota_version,
                "published_time": tracker_value(block, "Published Time"),
                "security_os": tracker_value(block, "Security Patch"),
                "region_code": requested_region.upper(),
            }
        )

    if not candidates:
        raise ValueError("OPlus-Tracker returned no full OTA download")
    candidates.sort(
        key=lambda item: (
            version_numbers(item["version_name"]),
            item["published_time"],
            item["security_os"],
        ),
        reverse=True,
    )

    code = requested_code.strip().upper()
    if re.fullmatch(r"CPH\d{4}IN", code):
        code = code[:-2]
    return code, candidates[0]


def aes_ctr(data, key, iv, decrypt=False):
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    worker = cipher.decryptor() if decrypt else cipher.encryptor()
    return worker.update(data) + worker.finalize()


def encrypted_protected_key(aes_key, public_key):
    key = serialization.load_pem_public_key(public_key.encode())
    encrypted = key.encrypt(
        base64.b64encode(aes_key),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode()


def query_ota_server(model, ota_version, region, mode):
    if region not in OTA_REGION_CONFIG:
        raise ValueError(f"Unsupported ColorOS region: {region}")
    host, language, carrier, key_region, key_version = OTA_REGION_CONFIG[region]
    aes_key = os.urandom(32)
    iv = os.urandom(16)
    device_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=64))
    protected = encrypted_protected_key(aes_key, OTA_PUBLIC_KEYS[key_region])

    headers = {
        "language": language,
        "newLanguage": language,
        "androidVersion": "unknown",
        "colorOSVersion": "unknown",
        "romVersion": "unknown",
        "infVersion": "1",
        "otaVersion": ota_version,
        "model": model,
        "mode": mode,
        "nvCarrier": carrier,
        "pipelineKey": "ALLNET",
        "operator": "ALLNET",
        "companyId": "",
        "version": "2",
        "deviceId": device_id,
        "Content-Type": "application/json; charset=utf-8",
        "protectedKey": json.dumps(
            {
                "SCENE_1": {
                    "protectedKey": protected,
                    "version": str(time.time_ns() + 86_400_000_000_000),
                    "negotiationVersion": key_version,
                }
            }
        ),
    }
    plain_body = json.dumps(
        {
            "mode": "0",
            "time": int(time.time() * 1000),
            "isRooted": "0",
            "isLocked": True,
            "type": "0",
            "deviceId": "0" * 64,
            "opex": {"check": True},
            "isSuportPki": False,
        }
    ).encode()
    payload = json.dumps(
        {
            "params": json.dumps(
                {
                    "cipher": base64.b64encode(aes_ctr(plain_body, aes_key, iv)).decode(),
                    "iv": base64.b64encode(iv).decode(),
                }
            )
        }
    ).encode()
    outer = None
    last_error = None
    for attempt in range(3):
        request = urllib.request.Request(
            f"https://{host}/update/v3",
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                outer = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"OTA query returned HTTP {exc.code}") from exc
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if outer is None:
        raise RuntimeError(f"OTA query failed after 3 attempts: {last_error}")

    status = int(outer.get("responseCode") or 0)
    if status != 200:
        return status, None
    encrypted_body = json.loads(outer["body"])
    decrypted = aes_ctr(
        base64.b64decode(encrypted_body["cipher"]),
        aes_key,
        base64.b64decode(encrypted_body["iv"]),
        decrypt=True,
    )
    return status, json.loads(decrypted.decode())


REGION_MODEL_SUFFIXES = {
    "in": "IN",
    "eu": "EU",
    "eea": "EEA",
    "roe": "ROE",
    "ru": "RU",
    "tr": "TR",
    "8d": "8D",
}


def query_with_region_alias(model, ota_version, region, mode):
    base_model = model.strip().upper()
    suffix = REGION_MODEL_SUFFIXES.get(region.lower(), "")
    variants = []
    if suffix and not base_model.endswith(suffix):
        variants.append(f"{base_model}{suffix}")
    variants.append(base_model)

    last_status = 0
    last_body = None
    for query_model in variants:
        status, body = query_ota_server(query_model, ota_version, region, mode)
        last_status, last_body = status, body
        if status == 200 and body:
            return status, body
    return last_status, last_body


def candidate_from_ota_body(body, region):
    components = body.get("components")
    if not isinstance(components, list) or len(components) != 1:
        return None
    packets = components[0].get("componentPackets")
    if not isinstance(packets, dict):
        return None
    check_url = str(packets.get("manualUrl") or "").strip()
    if not check_url.startswith(("http://", "https://")):
        return None
    published = body.get("publishedTime")
    if isinstance(published, (int, float)):
        published = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(published / 1000))
    return {
        "download_check_link": check_url,
        "version_name": body.get("realVersionName") or body.get("versionName"),
        "ota_version": body.get("realOtaVersion") or body.get("otaVersion"),
        "published_time": published or "",
        "security_os": body.get("securityPatch") or "",
        "region_code": region.upper(),
    }


def query_ota_candidates(device_code, region, anti=True):
    model = device_code.strip().upper()
    modes = ("taste", "manual") if anti else ("manual", "taste")
    candidates = []
    channels = ("A", "C", "F", "H", "J")

    def query_channel(channel):
        fake_ota = f"{model}_11.{channel}.01_0001_197001010000"
        print(f"Querying {model}_11.{channel} ({region})", file=sys.stderr)
        last_success_ota = ""
        for mode in modes:
            status, body = query_with_region_alias(model, fake_ota, region, mode)
            if status == 2004 and mode == "taste" and last_success_ota:
                status, body = query_with_region_alias(
                    model, last_success_ota, region, "taste"
                )
            if status == 200 and body:
                last_success_ota = str(body.get("otaVersion") or last_success_ota)
                candidate = candidate_from_ota_body(body, region)
                if candidate:
                    return candidate
        return None

    with ThreadPoolExecutor(max_workers=len(channels)) as executor:
        futures = {
            executor.submit(query_channel, channel): channel
            for channel in channels
        }
        for future in as_completed(futures):
            candidate = future.result()
            if candidate:
                candidates.append(candidate)

    if not candidates:
        raise ValueError(f"No full OTA found for {model} in region {region}")

    unique = {}
    for item in candidates:
        identity = (
            str(item.get("ota_version") or ""),
            str(item.get("version_name") or ""),
        )
        unique[identity] = item
    candidates = list(unique.values())
    candidates.sort(
        key=lambda item: (
            version_numbers(item["version_name"]),
            item["published_time"],
            item["security_os"],
        ),
        reverse=True,
    )
    return model, candidates


def query_latest_ota(device_code, region, anti=True):
    model, candidates = query_ota_candidates(device_code, region, anti)
    return model, candidates[0]


def query_all_regions(device_code, anti=True):
    model = device_code.strip().upper()
    candidates = []

    def query_region(region):
        _, items = query_ota_candidates(model, region, anti)
        return items[0]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(query_region, region): region
            for region in PUBLIC_OTA_REGIONS
        }
        for future in as_completed(futures):
            region = futures[future]
            try:
                candidates.append(future.result())
            except Exception as exc:
                print(f"No firmware for {model}/{region}: {exc}", file=sys.stderr)

    if not candidates:
        raise ValueError(f"No full OTA found for {model} in any region")
    candidates.sort(
        key=lambda item: (
            version_numbers(item["version_name"]),
            item["published_time"],
            item["security_os"],
            item["region_code"],
        ),
        reverse=True,
    )
    return model, candidates


def resolve_downloadcheck(url):
    if "downloadcheck" not in url.lower():
        return url

    headers = {**ANDROID_HEADERS, "Range": "bytes=0-"}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"downloadCheck returned HTTP {exc.code}") from exc
        location = (exc.headers.get("Location") or "").strip()
    else:
        try:
            location = (response.headers.get("Location") or "").strip()
            status = response.getcode()
            final_url = (response.geturl() or "").strip()
        finally:
            response.close()
        if status == 200 and final_url and final_url != url:
            return final_url
        if status == 200:
            follow_request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(follow_request, timeout=30) as follow_response:
                followed_url = (follow_response.geturl() or "").strip()
            if followed_url and followed_url != url:
                return followed_url
        if status not in (301, 302, 303, 307, 308):
            raise RuntimeError(f"downloadCheck returned HTTP {status}")

    if not location:
        raise RuntimeError("downloadCheck response is missing the Location header")
    return urllib.parse.urljoin(url, location)


def output_filename(data_code, item, resolved_url):
    name = urllib.parse.unquote(os.path.basename(urllib.parse.urlsplit(resolved_url).path))
    if name.lower().endswith((".zip", ".tgz")):
        return name
    version = re.sub(r"[^A-Za-z0-9._()-]+", "_", str(item.get("version_name") or "latest"))
    region = re.sub(r"[^A-Za-z0-9._-]+", "_", str(item.get("region_code") or "UNKNOWN"))
    return f"{data_code}_ota_full_{version}_{region}.zip"


def write_shell_info(path, values):
    with open(path, "w", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={shlex.quote(str(value or ''))}\n")


def write_candidate_list(path, data_code, candidates, scanned_regions):
    items = []
    for item in candidates:
        entry = dict(item)
        entry["filename"] = output_filename(
            data_code,
            item,
            str(item.get("resolved_link") or item.get("download_check_link") or ""),
        )
        items.append(entry)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "device_code": data_code,
                "scanned_regions": list(scanned_regions),
                "items": items,
            },
            stream,
            ensure_ascii=False,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("device_code")
    parser.add_argument("output")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--query", action="store_true")
    source.add_argument("--data-url")
    source.add_argument("--tracker-output")
    source.add_argument("--candidate-file")
    parser.add_argument("--region", default="")
    parser.add_argument("--anti", choices=("0", "1"), default="1")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all-regions", action="store_true")
    parser.add_argument("--index", type=int, default=1)
    args = parser.parse_args()

    try:
        if args.query:
            region = args.region.strip().lower()
            if not region and not args.all_regions:
                raise ValueError("--region is required with --query")
            if args.all_regions:
                data_code, candidates = query_all_regions(
                    args.device_code, anti=args.anti == "1"
                )
            else:
                data_code, candidates = query_ota_candidates(
                    args.device_code, region, anti=args.anti == "1"
                )
            if args.list:
                scanned_regions = PUBLIC_OTA_REGIONS if args.all_regions else (region,)
                write_candidate_list(
                    args.output, data_code, candidates, scanned_regions
                )
                return 0
            item = candidates[0]
            check_url = str(item["download_check_link"]).strip()
            resolved_url = str(item.get("resolved_link") or "").strip()
            if not resolved_url:
                resolved_url = resolve_downloadcheck(check_url)
        elif args.candidate_file:
            with open(args.candidate_file, "r", encoding="utf-8") as stream:
                candidate_data = json.load(stream)
            candidates = candidate_data.get("items")
            if not isinstance(candidates, list) or not candidates:
                raise ValueError("Candidate list is empty or invalid")
            if args.index < 1 or args.index > len(candidates):
                raise ValueError(
                    f"Candidate index must be between 1 and {len(candidates)}"
                )
            data_code = str(candidate_data.get("device_code") or args.device_code)
            item = candidates[args.index - 1]
            check_url = str(item["download_check_link"]).strip()
            resolved_url = str(item.get("resolved_link") or "").strip()
            if not resolved_url:
                resolved_url = resolve_downloadcheck(check_url)
        elif args.tracker_output:
            with open(args.tracker_output, "r", encoding="utf-8") as stream:
                data_code, item = select_tracker_ota(
                    stream.read(), args.device_code, args.region
                )
            check_url = str(item["download_check_link"]).strip()
            resolved_url = str(item["resolved_link"]).strip()
        else:
            if args.list:
                raise ValueError("--list can only be used with --query")
            data = read_json(args.data_url)
            if not isinstance(data, dict):
                raise ValueError("ColorOS OTA data root must be an object")
            data_code, item = select_ota(data, args.device_code, args.region)
            check_url = str(item.get("download_check_link") or "").strip()
            resolved_url = resolve_downloadcheck(check_url)
        if not resolved_url.startswith(("http://", "https://")):
            raise ValueError("Resolved ColorOS URL is not HTTP(S)")
        write_shell_info(
            args.output,
            {
                "coloros_data_code": data_code,
                "coloros_version": item.get("version_name"),
                "coloros_ota_version": item.get("ota_version"),
                "coloros_region": item.get("region_code"),
                "coloros_published": item.get("published_time"),
                "coloros_check_url": check_url,
                "coloros_resolved_url": resolved_url,
                "coloros_filename": output_filename(data_code, item, resolved_url),
            },
        )
    except Exception as exc:
        print(f"ColorOS resolve failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
