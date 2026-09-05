from uuid import UUID
from fastapi import UploadFile
from app.core.cache import TimedLruCache
from app.infrastructure.pdf_reader import PyMuPdfReader
from app.infrastructure.storage import LocalDocumentStorage

class DocumentService:
    def __init__(self, storage: LocalDocumentStorage, reader: PyMuPdfReader):
        self.storage, self.reader = storage, reader
        # Cache a page independently instead of caching only a requested range.
        # Reading page 2 after pages 1-2, for example, should never extract it
        # a second time.
        self._page_cache: TimedLruCache[tuple[UUID, int], dict[str, str | int]] = TimedLruCache(512)
    async def upload(self, file: UploadFile):
        return await self.storage.save(file)
    def pages(self, identifier: UUID, start: int, end: int):
        normalized_start, normalized_end = sorted((start, end))
        missing_runs: list[tuple[int, int]] = []
        run_start: int | None = None
        for page_number in range(normalized_start, normalized_end + 1):
            if self._page_cache.get((identifier, page_number)) is None:
                run_start = page_number if run_start is None else run_start
            elif run_start is not None:
                missing_runs.append((run_start, page_number - 1))
                run_start = None
        if run_start is not None:
            missing_runs.append((run_start, normalized_end))

        path = self.storage.path_for(identifier)
        for run_start, run_end in missing_runs:
            for page in self.reader.extract(path, run_start, run_end):
                self._page_cache.set((identifier, int(page["page"])), dict(page))

        return [
            dict(self._page_cache.get((identifier, page_number)) or {"page": page_number, "text": ""})
            for page_number in range(normalized_start, normalized_end + 1)
        ]
