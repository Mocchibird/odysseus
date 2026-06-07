from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_iris_vault_uploads_auto_sort_by_file_type():
    service = (ROOT / "src" / "iris_vault.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes" / "iris_vault_routes.py").read_text(encoding="utf-8")
    book_routes = (ROOT / "routes" / "book_routes.py").read_text(encoding="utf-8")
    book_service = (ROOT / "src" / "book_reader.py").read_text(encoding="utf-8")
    upload_routes = (ROOT / "routes" / "upload_routes.py").read_text(encoding="utf-8")
    upload_handler = (ROOT / "src" / "upload_handler.py").read_text(encoding="utf-8")
    file_handler = (ROOT / "static" / "js" / "fileHandler.js").read_text(encoding="utf-8")
    chat = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    notes = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    docs = (ROOT / "routes" / "document_routes.py").read_text(encoding="utf-8")
    email_routes = (ROOT / "routes" / "email_routes.py").read_text(encoding="utf-8")

    assert "def default_upload_rel_path" in service
    assert "40_Attachments/{folder}" in service
    assert "10_Notes/{safe_name}" in service
    assert "PdfReader" in service
    assert "zipfile.ZipFile" in service
    assert "index_content: bool = True" in service
    assert '"epub": "epubs"' in service
    assert 'ext == ".epub"' in service
    assert '"image": "images"' in service
    assert '"pdf": "pdf"' in service
    assert '"document": "documents"' in service
    assert "def _unique_rel_path" in service
    assert "BOOK_ATTACHMENT_DIR" in service
    assert "CONTEXT_SUBDIR_KEYWORDS" in service
    assert "BOOK_CONTEXT_KEYWORDS" in service
    assert "def book_upload_rel_path" in service
    assert "context: str = \"\"" in service
    assert "source: str = \"\"" in service
    assert "def move_file" in service
    assert "def sort_inbox" in service
    assert "INBOX_DIR_NAMES" in service
    assert "ODYSSEUS_IRIS_AUTO_SORT_INBOX" in routes
    assert '"/sort-inbox"' in routes
    assert "context: str = Form(\"\")" in routes
    assert "source: str = Form(\"vault\")" in routes
    assert "save_uploaded_file" in routes
    assert "file.content_type" in routes
    assert "book_upload_rel_path" in book_service
    assert "book_reader.save_uploaded_book" in book_routes
    assert '"/api/books"' in book_routes
    assert "def _mirror_upload_to_vault" in upload_handler
    assert "vault_rel_path" in upload_handler
    assert "vault_path" in upload_routes
    assert "context: str = Form(\"\")" in upload_routes
    assert "source: str = Form(\"upload\")" in upload_routes
    assert "uploadPending(options = {})" in file_handler
    assert "fd.append('context'" in file_handler
    assert "fd.append('source'" in file_handler
    assert "context: msg" in chat
    assert "source: 'chat'" in chat
    assert "source', 'vault'" in notes
    assert "_mirror_document_to_vault" in docs
    assert "10_Notes/documents/{doc.id}.md" in docs
    assert "Email compose attachment" in email_routes
    assert "vault_path" in email_routes


def test_notes_view_has_vault_browser_controls():
    notes_js = (ROOT / "static" / "js" / "notes.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert "notes-vault-toggle" in notes_js
    assert "notes-vault-upload" in notes_js
    assert "notes-vault-reindex" in notes_js
    assert "/api/iris-vault/files" in notes_js
    assert "/api/iris-vault/file?path=" in notes_js
    assert "/api/iris-vault/upload" in notes_js
    assert "stored under your username folder" not in notes_js
    assert "function _renderVaultFiles" in notes_js
    assert "function _visibleVaultFiles" in notes_js
    assert "function _buildVaultTree" in notes_js
    assert "function _renderVaultTreeNode" in notes_js
    assert "50_State\\/book_(progress|metadata)" in notes_js
    assert "notes-vault-folder" in notes_js
    assert "function _vaultDisplayTitle" in notes_js
    assert "function _vaultDisplayExcerpt" in notes_js
    assert ".notes-vault-file" in css
    assert ".notes-vault-folder" in css
    assert ".notes-vault-tree" in css
    assert "height: auto !important" in css
    assert "grid-template-columns: 34px minmax(0, 1fr) auto" in css
    assert "font-style: normal !important" in css
    assert ".notes-vault-reader" in css
