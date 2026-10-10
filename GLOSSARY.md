# Paper Reader

A personal reader for research papers that sends translation, summary, analysis, and chat
requests to an AI tool the user already subscribes to.

## AI access

**AI Backend**:
The local AI tool that Paper Reader hands every AI request to. Supported: Claude Code and
Hermes. OpenClaw is a planned future AI Backend.
_Avoid_: Provider, engine, AI selector

**Provider**:
The service that actually serves a model inside Hermes, such as openai-codex. Claude Code has
no Provider of its own.
_Avoid_: Backend, vendor

**Model**:
One specific language model an AI Backend uses, such as gpt-6-luna or claude-haiku-5-5.
_Avoid_: Engine, backend

**Task**:
A kind of AI request Paper Reader makes: translation, summary, anatomy, chat, explanation,
equation reading, or glossary building. Each AI Backend picks a Model per Task.
_Avoid_: Job, mode, feature

**Subscription access**:
Using an AI Backend only through the user's own subscription login, never through a paid API
key.
_Avoid_: Free tier, API access

## Papers

**Paper**:
One scholarly work the user reads, independent of which PDF file holds it.
_Avoid_: Document, doc, file

**Paper ID**:
The stable identity of a Paper: its DOI, else its arXiv ID, else its Zotero item key, else its
File Fingerprint. Two PDF files of the same work share one Paper ID.
_Avoid_: doc_id, paper hash, title hash

**File Fingerprint**:
The identity of one exact PDF file, derived from its bytes. Any change to the file gives a new
File Fingerprint.
_Avoid_: Paper ID, checksum

**Paper Source**:
Where Paper Reader finds Papers: a Library Folder the user picks, or Zotero.
_Avoid_: Import, provider

**Bibliographic Record**:
The title, authors, and year of a Paper, looked up from its DOI or arXiv ID, or read from the
PDF when no identifier exists.
_Avoid_: Metadata, citation

**Chat History**:
The record of questions and answers about one Paper, kept per Paper ID.
_Avoid_: Session, conversation log
