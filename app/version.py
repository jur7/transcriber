from pathlib import Path

# Base application version. Update this when you cut a new release.
__version__ = "0.1.0"


def _read_build_stamp() -> str:
    """Reads build timestamp from build.txt if present.
    This file is generated during Docker image build.
    Returns an empty string if not available (e.g., dev environment).
    """
    try:
        p = Path(__file__).with_name("build.txt")
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


__build__ = _read_build_stamp()


def version_string() -> str:
    """Returns a display-friendly version string.
    Example: 0.1.0+202501010930 or just 0.1.0 if no build stamp.
    """
    return f"{__version__}+{__build__}" if __build__ else __version__

