"""One-time local key setup; no key in arguments, repository or stdout."""

import argparse
import getpass
import json
import sys

from app.llm.credentials import CredentialStoreError, TARGET, delete_key, load_key, save_key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--set",action="store_true")
    modes.add_argument("--status",action="store_true")
    modes.add_argument("--delete",action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.set:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise CredentialStoreError("Hidden setup requires a local interactive terminal")
            key = getpass.getpass("硅基流动 API Key（隐藏输入，保存到 Windows 凭据管理器）: ").strip()
            save_key(key)
            del key
            status = "saved"
        elif args.delete:
            delete_key()
            status = "deleted"
        else:
            status = "present" if load_key() else "not_configured"
        print(json.dumps({"status":status,"target":TARGET,"key_displayed":False},indent=2))
        return 0
    except CredentialStoreError as exc:
        print(str(exc),file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
