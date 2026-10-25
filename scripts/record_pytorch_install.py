"""Record fixed installed packages and installer URLs/hashes from local pip reports."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.cli.cache_archive import file_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--torch-report", type=Path, required=True)
    parser.add_argument("--dependencies-report", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for path in (args.torch_report, args.dependencies_report):
        report = json.loads(path.read_text(encoding="utf-8"))
        for entry in report["install"]:
            name, installed = entry["metadata"]["name"], entry["metadata"]["version"]
            url = entry["download_info"]["url"]
            host = urlsplit(url).hostname
            if host not in {"download.pytorch.org", "download-r2.pytorch.org", "files.pythonhosted.org"}:
                raise ValueError("Unexpected installer origin")
            if name.lower() == "torch" and host not in {"download.pytorch.org", "download-r2.pytorch.org"}:
                raise ValueError("Torch must come from the declared official CPU source")
            if version(name) != installed:
                raise ValueError(f"Installed version differs: {name}")
            records.append({"name": name, "version": installed, "url": url,
                "sha256": entry["download_info"]["archive_info"]["hashes"]["sha256"]})
    root = Path(__file__).resolve().parents[1]
    result = {"environment": "project .venv only", "torch_version": version("torch"),
        "installer_reports_sha256": {path.name: file_sha256(path) for path in (args.torch_report, args.dependencies_report)},
        "artifacts": records, "limitations": "Installer provenance record; not an independently audited dependency supply chain."}
    destination = root / "config/pytorch-installation.json"
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(destination), "packages_recorded": len(records), "torch_version": result["torch_version"]}))


if __name__ == "__main__":
    main()
