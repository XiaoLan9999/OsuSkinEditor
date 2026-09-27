"""Standalone updater entry point; deliberately does not import the Qt app"""
import argparse
import json
import sys
from core.update_installer import run_update


def main(argv=None):
    parser = argparse.ArgumentParser(description="OsuSkinEditor update helper")
    parser.add_argument("--job", required=True)
    arguments = parser.parse_args(argv)
    result = run_update(arguments.job)
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
