# Paper ID is separate from File Fingerprint

A Paper is identified by its DOI, else its arXiv ID, else its Zotero item key, and only as a last
resort by the hash of its PDF bytes. Chat History, the translation glossary, and the library entry
hang off this Paper ID, so they survive the PDF file changing (annotations saved into the file, a
publisher's final version) and opening the same Paper from a Library Folder or from Zotero. Layout,
translation, and equation caches stay keyed by the File Fingerprint (the byte hash), because they are
only valid for the exact file they were computed from.

## Considered Options

- **Byte hash only (the original scheme).** Simple, but any edit to the PDF orphans the Paper's chat.
- **Hash of the title.** Rejected: titles read from the PDF text layer are unreliable. Across 112
  PDFs in the user's Zotero library, 30 titles were wrong (journal names, a single drop-cap letter,
  running headers), and 7 different papers all yielded the same title.
- **DOI first.** Chosen: a DOI or arXiv ID was found in 103 of 112 PDFs, 100 of 101 checked DOIs
  matched Zotero's record, and no two papers collided. For Papers opened from Zotero, Zotero's stored
  DOI is preferred over the one read from the PDF.

## Consequences

- A preprint and its published version get different Paper IDs and so separate Chat Histories.
- A supplementary-information PDF that prints the main article's DOI shares that article's Chat
  History; its caches stay separate because its File Fingerprint differs.
