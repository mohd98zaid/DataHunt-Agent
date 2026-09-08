import pytest
from datahunt.errors import DataHuntError, ErrorCode
from datahunt.policy import (
    validate_url_scheme_and_format,
    validate_url_ssrf,
    check_domain_policy,
    is_public_business_email,
    escape_csv_formula
)

def test_scheme_validation():
    assert validate_url_scheme_and_format("https://example.com") == "https://example.com"
    assert validate_url_scheme_and_format("http://example.com") == "http://example.com"
    
    with pytest.raises(DataHuntError) as exc:
        validate_url_scheme_and_format("file:///etc/passwd")
    assert exc.value.code == ErrorCode.FETCH_BLOCKED

    with pytest.raises(DataHuntError) as exc:
        validate_url_scheme_and_format("data:text/html,<h1>test</h1>")
    assert exc.value.code == ErrorCode.FETCH_BLOCKED

def test_ssrf_ip_blocking():
    blocked_ips = [
        "http://127.0.0.1:8080",
        "http://10.0.0.1",
        "http://172.16.0.1",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
    ]
    for url in blocked_ips:
        with pytest.raises(DataHuntError) as exc:
            validate_url_ssrf(url)
        assert exc.value.code == ErrorCode.FETCH_BLOCKED

def test_domain_policy():
    # Blocked domains
    with pytest.raises(DataHuntError):
        check_domain_policy("spam.com", blocked_domains=["spam.com"])
    with pytest.raises(DataHuntError):
        check_domain_policy("sub.spam.com", blocked_domains=["spam.com"])

    # Allowed domains
    check_domain_policy("official.com", allowed_domains=["official.com"])
    with pytest.raises(DataHuntError):
        check_domain_policy("other.com", allowed_domains=["official.com"])

def test_public_business_email():
    assert is_public_business_email("careers@acme.com") is True
    assert is_public_business_email("info@acme.com") is True
    assert is_public_business_email("support@tech.ae") is True
    assert is_public_business_email("john.doe@gmail.com") is False
    assert is_public_business_email("alice@yahoo.com") is False

def test_csv_formula_escaping():
    assert escape_csv_formula("=SUM(A1:A10)") == "'=SUM(A1:A10)"
    assert escape_csv_formula("+12345") == "'+12345"
    assert escape_csv_formula("-cmd|/C calc.exe") == "'-cmd|/C calc.exe"
    assert escape_csv_formula("@link") == "'@link"
    assert escape_csv_formula("Normal Text") == "Normal Text"
