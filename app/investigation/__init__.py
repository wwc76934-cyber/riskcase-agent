"""Multi-source investigation development track (roadmap stage one)."""
from pathlib import Path
import site

# The project's fallback runtime uses a --target dependency directory. PYTHONPATH
# alone does not process pywin32's .pth bootstrap; a normal venv needs no override.
_addons = Path(__file__).resolve().parents[2] / '.venv-service-addons'
if _addons.is_dir():
    site.addsitedir(str(_addons))
