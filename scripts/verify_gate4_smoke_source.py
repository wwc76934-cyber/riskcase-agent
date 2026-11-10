"""Verify or reconstruct the original Gate 4 smoke application bytes from Git."""
import argparse
from hashlib import sha1, sha256
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


def verify(manifest_path, output=None):
    manifest = json.loads(Path(manifest_path).read_bytes())
    revision = manifest["git_mirror"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a Git commit fingerprint")
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", revision, "app"], cwd=root)
    expected_paths = {p for p in tracked.decode("utf-8").splitlines() if p.endswith(".py")}
    if set(manifest["files"]) != expected_paths:
        raise ValueError("Historical application path set changed")
    inventory, restored = [], {}
    for name, spec in sorted(manifest["files"].items()):
        path = PurePosixPath(name)
        if not path.parts or path.parts[0] != "app" or ".." in path.parts or "\\" in name:
            raise ValueError("Unexpected source path")
        stored = subprocess.check_output(["git", "cat-file", "blob", f"{revision}:{name}"], cwd=root)
        canonical = stored.replace(b"\r\n", b"\n")
        git_bytes = b"blob " + str(len(canonical)).encode() + b"\0" + canonical
        if sha1(git_bytes).hexdigest() != spec["git_blob"]:
            raise ValueError(f"Canonical Git content changed for {name}")
        mode = spec["crlf_lines"]
        if mode == "crlf":
            raw = canonical.replace(b"\n", b"\r\n")
        else:
            lines = canonical.splitlines(keepends=True)
            for number in mode:
                if type(number) is not int or not 1 <= number <= len(lines) or not lines[number - 1].endswith(b"\n"):
                    raise ValueError(f"Invalid line ending map for {name}")
                lines[number - 1] = lines[number - 1][:-1] + b"\r\n"
            raw = b"".join(lines)
        checksum = sha256(raw).hexdigest()
        if checksum != spec["raw_sha256"]:
            raise ValueError(f"Historical file bytes changed for {name}")
        inventory.append((name, checksum))
        restored[name] = raw
    binding = sha256(json.dumps(inventory, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    if binding != manifest["application_inventory_sha256"]:
        raise ValueError("Historical source inventory mismatch")
    if output is not None:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        for name, raw in restored.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(raw)
    return {"files": len(inventory), "application_inventory_sha256": binding}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.manifest, args.output)))
