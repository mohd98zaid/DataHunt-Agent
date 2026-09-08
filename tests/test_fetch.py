import pytest
from datahunt.tools.fetch import FetchTool, clean_html_to_text
from datahunt.errors import ErrorCode

def test_clean_html():
    raw_html = """
    <html>
      <head>
        <title>Job Page Title</title>
        <script>alert("malicious script");</script>
        <style>.hide { display: none; }</style>
      </head>
      <body>
        <nav><a href="/home">Home</a></nav>
        <h1>Senior Developer</h1>
        <p>We are looking for a Python developer.</p>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    title, text = clean_html_to_text(raw_html)
    assert title == "Job Page Title"
    assert "alert" not in text
    assert "display: none" not in text
    assert "Home" not in text
    assert "Copyright" not in text
    assert "Senior Developer" in text
    assert "Python developer" in text

def test_fetch_mock_and_limits():
    mock_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
    tool = FetchTool(
        max_bytes=100,
        mock_responses={"https://mock.site/page": mock_html}
    )
    res = tool.execute("https://mock.site/page")
    assert res.success is True
    assert res.data.title == "Test"
    assert res.data.extracted_text == "Test\nHello world"

def test_fetch_ssrf_blocking():
    tool = FetchTool()
    res = tool.execute("http://127.0.0.1:8000/private")
    assert res.success is False
    assert res.error_code == ErrorCode.FETCH_BLOCKED.value
