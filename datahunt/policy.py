import ipaddress
import re
import socket
from urllib.parse import urlparse
from typing import List, Optional, Tuple
from datahunt.errors import DataHuntError, ErrorCode

# Standard non-routable / sensitive IP networks for SSRF protection
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),       # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),        # Loopback
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / Cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),      # Private
    ipaddress.ip_network("192.0.0.0/24"),       # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),     # Private
    ipaddress.ip_network("198.18.0.0/15"),      # Network benchmark tests
    ipaddress.ip_network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),        # Multicast
    ipaddress.ip_network("240.0.0.0/4"),        # Reserved
    ipaddress.ip_network("255.255.255.255/32"), # Broadcast
    # IPv6
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),            # Loopback
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped
    ipaddress.ip_network("64:ff9b::/96"),       # IPv4/IPv6 translation
    ipaddress.ip_network("100::/64"),           # Discard prefix
    ipaddress.ip_network("2001:db8::/32"),      # Documentation
    ipaddress.ip_network("fc00::/7"),           # Unique local
    ipaddress.ip_network("fe80::/10"),          # Link-local
    ipaddress.ip_network("ff00::/8"),           # Multicast
]

# Generic business mailboxes
GENERIC_BUSINESS_PREFIXES = {
    "info", "support", "careers", "jobs", "hiring", "sales", "contact",
    "press", "hr", "hello", "team", "office", "media", "help", "inquiries",
    "recruiting", "recruitment", "talent", "people", "partnerships", "work", "apply"
}

def validate_url_scheme_and_format(url: str, allow_http: bool = True) -> str:
    """Validate URL scheme, rejecting non-web schemes."""
    if not url or not isinstance(url, str):
        raise DataHuntError(
            ErrorCode.INVALID_REQUEST,
            user_message="URL must be a non-empty string.",
            operator_message=f"Invalid URL input: {url}"
        )
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    allowed_schemes = ("https", "http") if allow_http else ("https",)
    if scheme not in allowed_schemes:
        raise DataHuntError(
            ErrorCode.FETCH_BLOCKED,
            user_message=f"URL scheme '{scheme}' is not permitted.",
            operator_message=f"SSRF policy blocked scheme '{scheme}' for {url}"
        )
    if not parsed.netloc:
        raise DataHuntError(
            ErrorCode.INVALID_REQUEST,
            user_message="URL missing hostname.",
            operator_message=f"Missing netloc in URL: {url}"
        )
    return url.strip()

def check_ip_ssrf(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Check if an IP address falls into any blocked or private range."""
    # Check if special/private attributes are set
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_unspecified or ip_obj.is_reserved:
        raise DataHuntError(
            ErrorCode.FETCH_BLOCKED,
            user_message="Access to private or local network addresses is prohibited.",
            operator_message=f"SSRF blocked private/loopback/reserved IP: {ip_obj}"
        )
    for net in BLOCKED_IP_NETWORKS:
        if ip_obj in net:
            raise DataHuntError(
                ErrorCode.FETCH_BLOCKED,
                user_message="Access to private or local network addresses is prohibited.",
                operator_message=f"SSRF blocked IP {ip_obj} matching network {net}"
            )

def validate_url_ssrf(url: str, allow_http: bool = True) -> Tuple[str, str]:
    """
    Validate URL and perform DNS resolution checks to defend against SSRF.
    Returns (cleaned_url, validated_ip).
    """
    cleaned_url = validate_url_scheme_and_format(url, allow_http=allow_http)
    parsed = urlparse(cleaned_url)
    hostname = parsed.hostname
    if not hostname:
        raise DataHuntError(
            ErrorCode.INVALID_REQUEST,
            user_message="Invalid URL hostname.",
            operator_message=f"Cannot extract hostname from URL {url}"
        )
    
    # Check if hostname is an explicit IP literal
    try:
        ip_obj = ipaddress.ip_address(hostname)
        check_ip_ssrf(ip_obj)
        return cleaned_url, str(ip_obj)
    except ValueError:
        pass # It is a domain name, proceed to DNS resolution

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    resolved_ips = []
    try:
        addr_info = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            check_ip_ssrf(ip_obj)
            resolved_ips.append(ip_str)
    except socket.gaierror as e:
        raise DataHuntError(
            ErrorCode.FETCH_DNS_FAILED,
            user_message=f"Could not resolve host '{hostname}'.",
            operator_message=f"DNS resolution failed for {hostname}: {e}"
        )

    if not resolved_ips:
        raise DataHuntError(
            ErrorCode.FETCH_DNS_FAILED,
            user_message=f"No IP addresses resolved for '{hostname}'.",
            operator_message=f"Zero resolved IPs for {hostname}"
        )

    return cleaned_url, resolved_ips[0]

def check_domain_policy(
    domain: str,
    allowed_domains: Optional[List[str]] = None,
    blocked_domains: Optional[List[str]] = None
) -> None:
    """Enforce allowed and blocked domain rules."""
    domain_lower = domain.lower()
    domain_lower = domain_lower.rstrip('.')
    
    # Check blocked domains
    if blocked_domains:
        for blocked in blocked_domains:
            b = blocked.lower()
            if domain_lower == b or domain_lower.endswith("." + b):
                raise DataHuntError(
                    ErrorCode.FETCH_BLOCKED,
                    user_message=f"Domain '{domain}' is blocked by policy.",
                    operator_message=f"Domain {domain} matched blocked domain {blocked}"
                )

    # Check allowed domains
    if allowed_domains and len(allowed_domains) > 0:
        matched = False
        for allowed in allowed_domains:
            a = allowed.lower()
            if domain_lower == a or domain_lower.endswith("." + a):
                matched = True
                break
        if not matched:
            raise DataHuntError(
                ErrorCode.FETCH_BLOCKED,
                user_message=f"Domain '{domain}' is not in the allowed domains list.",
                operator_message=f"Domain {domain} not in allowed list: {allowed_domains}"
            )

def is_public_business_email(email: str) -> bool:
    """
    Check if an email conforms to the public business contact policy:
    Only generic/role-based business mailboxes on official domains are accepted.
    """
    if not email or "@" not in email:
        return False
    parts = email.lower().split("@")
    if len(parts) != 2:
        return False
    local_part, domain_part = parts
    # Check if local part is a generic mailbox
    if local_part in GENERIC_BUSINESS_PREFIXES:
        return True
    # Common consumer email domains are not business emails
    consumer_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "mail.com"}
    if domain_part in consumer_domains:
        return False
    return False

def escape_csv_formula(val: Any) -> str:
    """
    Neutralize CSV / Excel formula injection risk by prepending a single quote
    if a cell starts with '=', '+', '-', '@', '\t', or '\r', including when preceded by whitespace.
    """
    if val is None:
        return ""
    text = str(val)
    if not text:
        return ""
    stripped = text.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r", "%"):
        return "'" + text
    return text
