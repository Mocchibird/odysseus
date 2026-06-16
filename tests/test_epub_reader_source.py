from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_epub_reader_backend_routes_and_progress_notes_exist():
    service = (ROOT / "src" / "epub_reader.py").read_text(encoding="utf-8")
    book_service = (ROOT / "src" / "book_reader.py").read_text(encoding="utf-8")
    store = (ROOT / "src" / "book_store.py").read_text(encoding="utf-8")
    book_routes = (ROOT / "routes" / "book_routes.py").read_text(encoding="utf-8")
    middleware = (ROOT / "core" / "middleware.py").read_text(encoding="utf-8")

    # EPUB parsing (unchanged) — now backed by the native Books store, not a vault.
    assert "zipfile.ZipFile" in service
    assert "META-INF/container.xml" in service
    assert "parse_epub_toc" in service
    assert "read_epub_chapter" in service
    assert "book_store" in service
    assert "iris_vault" not in service          # vault fully decoupled
    # book_reader is a thin facade over the native store; no vault, no MD mirrors.
    assert "iris_vault" not in book_service
    assert "book_store" in book_service
    assert "def save_progress" in book_service
    assert "def save_title" in book_service
    assert "PdfReader" in book_service
    assert "SUPPORTED_BOOK_EXTENSIONS" in book_service
    assert "def pdf_file_path" in book_service
    # the store is the native Books store — it owns its bytes (BOOKS_DIR) and
    # extracted text (RAG-indexed under kind="book"), decoupled from Knowledge.
    assert "knowledge_base" not in store          # decoupled from the KB
    assert "content_extract" in store             # shared text extraction
    assert "content_rag" in store                 # shared RAG indexing (kind="book")
    assert "BookProgress" in store and "BookAnnotation" in store  # reading state
    assert "def resolve_book_file" in store
    assert "BOOKS_DIR" in store                   # book bytes live in the native store
    # routes unchanged (still call book_reader, still serve PDFs inline).
    assert 'prefix="/api/books"' in book_routes
    assert '"/upload"' in book_routes
    assert '"/chapter"' in book_routes
    assert '"/file"' in book_routes
    assert "FileResponse" in book_routes
    assert 'media_type="application/pdf"' in book_routes
    assert 'path == "/api/books/file"' in middleware
    assert 'response.headers["X-Frame-Options"] = "SAMEORIGIN"' in middleware
    assert "frame-ancestors 'self'" in middleware
    assert "asyncio.to_thread" in book_routes  # ingest into the KB off the event loop
    assert '"/open"' in book_routes
    assert '"/progress"' in book_routes
    assert "BookTitleRequest" in book_routes
    assert '"/title"' in book_routes


def test_books_has_dedicated_reader_ui_hooks():
    books = (ROOT / "static" / "js" / "books.js").read_text(encoding="utf-8")
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    # Shipped CSS = style.css + fork.css (fork-only rules were extracted into
    # fork.css to keep style.css aligned with upstream; see fork-ui.js strategy).
    css = ((ROOT / "static" / "style.css").read_text(encoding="utf-8")
           + (ROOT / "static" / "fork.css").read_text(encoding="utf-8"))
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

    # Books is now its OWN standalone tool window (books.js), no longer a mode
    # inside the Notes pane. It uses the same modal lifecycle as knowledge.js /
    # health.js, and the reader was moved out of notes.js into here.
    assert "Compatibility shim" not in books          # the shim is gone — this is the real module
    assert "openBooksPanel" in books
    assert "books-modal" in books
    assert "Modals.register('books-modal'" in books
    assert "makeToolModalDraggable" in books
    # The reader logic (PDF continuous-scroll + EPUB chapter streaming) lives here.
    assert "/api/books/open?path=" in books
    assert "/api/books/chapter?path=" in books
    assert "/api/books/progress" in books
    assert "/api/books/upload" in books
    assert "/api/books/title" in books
    assert "/api/books/file?path=" in books
    assert "_renameBook" in books
    assert "notes-book-pdf-frame" in books
    assert "createPdfReader(" in books  # continuous-scroll PDF via pdfReader.js (no native iframe)
    assert "function _bookUsesContinuousScroll" in books
    assert "function _appendNextBookChapterIfNeeded" in books
    assert "BOOK_CONTINUOUS_MAX_RENDERED_CHAPTERS" in books
    assert "function _trimBookContinuousStream" in books
    assert "delete _bookOpenBook.chapters[idx].html" in books
    assert "notes-book-chapter-section" in books
    assert "notes-book-page" in books
    assert "XMLHttpRequest" in books
    assert "xhr.upload.onprogress" in books
    assert "accept = '.epub,.pdf" in books
    assert "bookToolsModule" in books  # bookmarks/highlights/in-book search/read-aloud

    # notes.js is Notes-only now — every Books hook has been removed.
    assert "openBooksPanel" not in notes
    assert "_notesMode" not in notes
    assert "notes-books-toggle" not in notes
    assert "_fetchBooks" not in notes
    assert "createPdfReader" not in notes
    assert "_bookOpenBook" not in notes
    assert "_removeLegacyBooksModal" not in notes

    # Buttons stay in index.html; books.js is loaded via app.js (not a direct
    # <script> in index), so a stale cached index can't import it twice.
    assert "tool-books-btn" in index
    assert "rail-books" in index
    assert "/static/js/books.js" not in index

    # app.js opens the standalone window via booksModule — no legacy-modal shim.
    assert "booksModule" in app
    assert "booksModule.openBooksPanel" in app
    assert "_removeLegacyBooksModal" not in app

    # CSS: the standalone window shell + the reader classes it reuses.
    assert "#books-modal" in css
    assert ".books-modal-content" in css
    assert ".books-modal-body" in css
    assert ".books-reader-view" in css
    assert ".notes-book-content" in css
    assert ".notes-book-controls-row" in css
    assert ".notes-book-page" in css
    assert ".notes-book-upload-track" in css
    assert ".notes-book-row-title" in css
    assert ".notes-book-title-edit" in css
    assert ".notes-book-pdf-viewer" in css
    assert ".notes-book-pdf-frame" in css
    assert ".notes-book-controls-row-pdf" in css
    assert ".notes-book-content-continuous" in css
    assert ".notes-book-chapter-section" in css

    # Cache is versioned (the exact number bumps every release — don't pin it).
    assert "odysseus-v" in sw
    assert "/static/style.css?v=" in index
    assert "/static/app.js?v=" in index
    assert "./js/books.js?v=" in app
    assert "./js/notes.js?v=" in app


def test_agent_tool_registration_for_books():
    # upstream #3435 moved tool execution into the src/agent_tools/ package
    # (agent_tools.py -> agent_tools/__init__.py); the manage_books tag lives there.
    agent_tools = (ROOT / "src" / "agent_tools" / "__init__.py").read_text(encoding="utf-8")
    schemas = (ROOT / "src" / "tool_schemas.py").read_text(encoding="utf-8")
    execution = (ROOT / "src" / "tool_execution.py").read_text(encoding="utf-8")
    index = (ROOT / "src" / "tool_index.py").read_text(encoding="utf-8")

    assert '"manage_books"' in agent_tools
    assert '"name": "manage_books"' in schemas
    assert "do_manage_books" in execution
    assert "manage_books" in index


def test_markdown_hidden_answer_quiz_syntax_exists():
    markdown = (ROOT / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    # Shipped CSS = style.css + fork.css (fork-only rules were extracted into
    # fork.css to keep style.css aligned with upstream; see fork-ui.js strategy).
    css = ((ROOT / "static" / "style.css").read_text(encoding="utf-8")
           + (ROOT / "static" / "fork.css").read_text(encoding="utf-8"))

    assert "quiz/cloze syntax" in markdown
    assert "spoiler and quiz/cloze syntax" in markdown
    assert "flashcard syntax" in markdown
    assert "quiz-reveal" in markdown
    assert "quiz-spoiler" in markdown
    assert "quiz-flashcard" in markdown
    assert "Reveal hidden answer" in markdown
    assert "Reveal spoiler" in markdown
    assert "aria-expanded" in markdown
    assert "Reveal flashcard answer" in markdown
    assert ".quiz-reveal" in css
    assert ".quiz-reveal.revealed" in css
    assert ".quiz-spoiler" in css
    assert ".quiz-flashcard" in css
    assert ".quiz-flashcard.revealed" in css
