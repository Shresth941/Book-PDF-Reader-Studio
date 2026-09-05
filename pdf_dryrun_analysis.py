import os, fitz

path = r'C:\PDF Reader\docs\None_Form1_Declaration_ESI.pdf'
print('FILE_EXISTS=', os.path.exists(path))
if not os.path.exists(path):
    raise SystemExit(1)

doc = fitz.open(path)
print('PAGE_COUNT=', doc.page_count)
print('PDF_IS_ENCRYPTED=', doc.is_encrypted)

for i in range(min(3, doc.page_count)):
    page = doc.load_page(i)
    text = page.get_text('text')
    words = page.get_text('words')
    blocks = page.get_text('blocks')
    images = page.get_images(full=True)
    print(f'PAGE_{i+1}_TEXT_CHARS=', len(text))
    print(f'PAGE_{i+1}_WORDS=', len(words))
    print(f'PAGE_{i+1}_TEXT_BLOCKS=', len(blocks))
    print(f'PAGE_{i+1}_IMAGES=', len(images))
    print('PREVIEW_START')
    print(repr(text[:1500]))
    print('PREVIEW_END')
    print('---')
