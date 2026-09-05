from typing import Literal

from pydantic import BaseModel, Field

class TranslateRequest(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(pattern="^(auto|en|hi)$")
    target: str = Field(pattern="^(en|hi)$")
    document_title: str = ""

class DefinitionRequest(BaseModel):
    term: str = Field(min_length=1)
    language: str = Field(pattern="^(auto|en|hi)$")
    # Context is optional and intentionally has no small character cap: a
    # user may select a full sentence or paragraph from a PDF.
    context: str = ""
    document_title: str = ""
    document_id: str = ""


class ExportRequest(BaseModel):
    text: str = Field(min_length=1)
    format: Literal["txt", "docx", "pdf"]
