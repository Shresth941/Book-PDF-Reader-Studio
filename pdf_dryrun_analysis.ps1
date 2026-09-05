$python = "C:\PDF Reader\backend\.venv\Scripts\python.exe"
$script = @'
import os, fitz
path = r'C:\PDF Reader\docs\None_Form1_Declaration_ESI.pdf'
print('exists=', os.path.exists(path))
doc = fitz.open(path)
print('page_count=', doc.page_count)
print('encrypted=', doc.is_encrypted)
for i in range(min(3, doc.page_count)):
    page = doc.load_page(i)
    text = page.get_text('text')
    words = page.get_text('words')
    blocks = page.get_text('blocks')
    imgs = page.get_images(full=True)
    print(f'page_{i + 1}_chars={len(text)}')
    print(f'page_{i + 1}_words={len(words)}')
    print(f'page_{i + 1}_blocks={len(blocks)}')
    print(f'page_{i + 1}_images={len(imgs)}')
    print(repr(text[:1200]))
    print('-----')
'@
& $python -c $script
