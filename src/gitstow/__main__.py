"""Allow running as `python -m gitstow`.

Useful on Windows when the Scripts directory is not on PATH:

    python -m gitstow onboard
    python -m gitstow add owner/repo
"""

from gitstow.cli.main import app

app()
