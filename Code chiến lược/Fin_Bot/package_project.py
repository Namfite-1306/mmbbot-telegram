import os
import zipfile

PROJECT_DIR = r"C:\Users\admin\OneDrive\Fin_Bot"
OUTPUT_ZIP = r"C:\Users\admin\OneDrive\Fin_Bot_Final.zip"

EXCLUDE_DIRS = {
    ".venv",
    "__pycache__",
    ".git",
}

EXCLUDE_FILES = {
    ".env",
}

with zipfile.ZipFile(
    OUTPUT_ZIP,
    "w",
    zipfile.ZIP_DEFLATED
) as zipf:

    for root, dirs, files in os.walk(PROJECT_DIR):

        # Loại thư mục không cần gửi
        dirs[:] = [
            d for d in dirs
            if d not in EXCLUDE_DIRS
        ]

        for file in files:

            if file in EXCLUDE_FILES:
                continue

            full_path = os.path.join(root, file)

            relative_path = os.path.relpath(
                full_path,
                os.path.dirname(PROJECT_DIR)
            )

            zipf.write(
                full_path,
                relative_path
            )

print("=" * 60)
print("ĐÃ ĐÓNG GÓI PROJECT")
print("=" * 60)
print(OUTPUT_ZIP)

size_mb = os.path.getsize(OUTPUT_ZIP) / (1024 * 1024)

print(f"Kích thước ZIP: {size_mb:.2f} MB")