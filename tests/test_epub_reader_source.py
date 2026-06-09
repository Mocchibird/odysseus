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
    # the store owns bytes (DATA_DIR/books) + DB tables + book-text RAG indexing.
    assert "BookFile" in store and "BookProgress" in store and "BookAnnotation" in store
    assert "BOOKS_DIR" in store
    assert "def resolve_book_file" in store
    assert 'RAG_KIND = "book"' in store
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
    assert "BackgroundTasks" in book_routes
    assert "index_content=False" in book_routes
    assert "background_tasks.add_task" in book_routes
    assert '"/open"' in book_routes
    assert '"/progress"' in book_routes
    assert "BookTitleRequest" in book_routes
    assert '"/title"' in book_routes


def test_books_has_dedicated_reader_ui_hooks():
    books = (ROOT / "static" / "js" / "books.js").read_text(encoding="utf-8")
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")

    assert "Compatibility shim" in books
    assert "openBooksPanel" in books
    assert "books-modal" in books
    assert "tool-books-btn" in index
    assert "rail-books" in index
    assert "/static/js/books.js" not in index
    assert "notes-books-toggle" in notes
    assert "openBooksPanel" in notes
    assert "/api/books/open?path=" in notes
    assert "/api/books/chapter?path=" in notes
    assert "/api/books/progress" in notes
    assert "/api/books/upload" in notes
    assert "/api/books/title" in notes
    assert "/api/books/file?path=" in notes
    assert "_renameBook" in notes
    assert "notes-book-pdf-frame" in notes
    assert "createPdfReader(" in notes  # continuous-scroll PDF via pdfReader.js (no native iframe)
    assert "_viewMode === 'grid' && !isVault && !isBooks" in notes
    assert "function _bookUsesContinuousScroll" in notes
    assert "function _appendNextBookChapterIfNeeded" in notes
    assert "BOOK_CONTINUOUS_MAX_RENDERED_CHAPTERS" in notes
    assert "function _trimBookContinuousStream" in notes
    assert "delete _bookOpenBook.chapters[idx].html" in notes
    assert "notes-book-chapter-section" in notes
    assert "_setBookChapter(_bookChapterIndex + 1).finally" not in notes
    assert "notes-header-spacer" in notes
    assert "archiveToggle" in notes
    assert "viewToggle" in notes
    assert "pane?.classList.remove('notes-pane-archive')" in notes
    assert "notes-book-page" in notes
    assert "XMLHttpRequest" in notes
    assert "xhr.upload.onprogress" in notes
    assert "accept = '.epub,.pdf" in notes
    assert "notesModule.openBooksPanel" in app
    assert "_removeLegacyBooksModal" in app
    assert "_removeLegacyBooksModal" in notes
    assert "#books-modal" in css
    assert "display: none !important" in css
    # Cache is versioned (the exact number bumps every release — don't pin it).
    assert "odysseus-v" in sw
    assert "/static/style.css?v=" in index
    assert "/static/app.js?v=" in index
    assert "./js/notes.js?v=" in app
    assert ".notes-pane-books" in css
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
    assert ".notes-pane-header .doc-action-icon-btn.notes-header-spacer" in css
    assert "visibility: hidden" in css


def test_agent_tool_registration_for_books():
    agent_tools = (ROOT / "src" / "agent_tools.py").read_text(encoding="utf-8")
    schemas = (ROOT / "src" / "tool_schemas.py").read_text(encoding="utf-8")
    execution = (ROOT / "src" / "tool_execution.py").read_text(encoding="utf-8")
    index = (ROOT / "src" / "tool_index.py").read_text(encoding="utf-8")

    assert '"manage_books"' in agent_tools
    assert '"name": "manage_books"' in schemas
    assert "do_manage_books" in execution
    assert "manage_books" in index


def test_markdown_hidden_answer_quiz_syntax_exists():
    markdown = (ROOT / "static" / "js" / "markdown.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

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
