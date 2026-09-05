from pathlib import Path
import fitz
from app.infrastructure.pdf_reader import PyMuPdfReader

cases = [
    ("1_to_12", 1, 12, True),
    ("1_to_2", 1, 2, True),
    ("100_to_120", 100, 120, True),
    ("100_to_200", 100, 200, True),
    ("20_to_80", 20, 80, True),
    ("reversed_12_to_1", 12, 1, True),
    ("reversed_120_to_100", 120, 100, True),
    ("invalid_0_to_5", 0, 5, False),
    ("invalid_201_to_250", 201, 250, False),
]

pdf_path = Path("tmp_range_test.pdf")
with fitz.open() as doc:
    for i in range(1, 201):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i}")
    doc.save(pdf_path)

reader = PyMuPdfReader()
for name, start, end, should_pass in cases:
    try:
        result = reader.extract(pdf_path, start, end)
        pages = [item["page"] for item in result]
        if should_pass:
            print(f"{name}: PASS | range={start}-{end} -> {pages[0]}..{pages[-1]} count={len(result)}")
        else:
            print(f"{name}: FAIL | expected ValueError but got pages={pages[0]}..{pages[-1]} count={len(result)}")
    except ValueError:
        if should_pass:
            print(f"{name}: FAIL | expected success but raised ValueError")
        else:
            print(f"{name}: PASS | raised ValueError as expected")
    except Exception as exc:
        print(f"{name}: FAIL | unexpected {type(exc).__name__}: {exc}")

pdf_path.unlink(missing_ok=True)
