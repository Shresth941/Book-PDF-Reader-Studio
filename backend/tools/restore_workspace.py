"""Run from backend: python -m tools.restore_workspace backup.zip NEW_DIRECTORY"""
import argparse
from pathlib import Path
from app.services.review_backup import restore_backup

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify and restore a PDF Reader backup into a new directory.")
    parser.add_argument("backup", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(restore_backup(args.backup, args.destination))
