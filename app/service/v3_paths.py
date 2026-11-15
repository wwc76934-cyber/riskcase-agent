"""Native long paths for local V3 archives; does not change legacy storage."""
from pathlib import Path
import os

def native_archive_path(value):
    path=Path(value).resolve()
    raw=str(path)
    if os.name=='nt' and not raw.startswith('\\\\?\\'):
        return Path('\\\\?\\UNC\\'+raw[2:] if raw.startswith('\\\\') else '\\\\?\\'+raw)
    return path
