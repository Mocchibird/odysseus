# src/tool_index_fork.py
"""Fork-only tool-index additions (always-available sets, tool descriptions,
description rewords, and keyword-routing hints).

Merged into src/tool_index.py at import so upstream's ALWAYS_AVAILABLE /
ASSISTANT_ALWAYS_AVAILABLE / BUILTIN_TOOL_DESCRIPTIONS / ToolIndex._KEYWORD_HINTS
literals stay byte-identical to upstream. See docs/fork-additive-policy.md.
"""

# Net-new fork tools the agent should always be able to reach.
FORK_ALWAYS_AVAILABLE = frozenset({
    'adopt_served_model',
    'api_call',
    'app_api',
    'list_cached_models',
    'list_cookbook_servers',
    'list_serve_presets',
    'list_served_models',
    'manage_health',
    'search_files',
    'send_ping',
    'serve_model',
    'serve_preset',
    'stop_served_model',
    'tail_serve_output',
    'web_fetch',
    'web_search',
})

# Same, for the personal-assistant tool surface.
FORK_ASSISTANT_ALWAYS_AVAILABLE = frozenset({
    'manage_health',
    'search_files',
    'send_ping',
})

# Descriptions for the fork's net-new builtin tools.
FORK_TOOL_DESCRIPTIONS = {
    'send_ping': "Send an immediate ntfy push notification/ping to the user through the configured ntfy integration and reminder topic. Use for 'ping me now', 'send me a notification', or proactive assistant pings. For reminders at a future time, use manage_notes with due_date.",
    'search_files': "Search the user's content — their Files (uploaded docs), Books (PDF/EPUB), and authored Documents — to recall facts/specs/notes. Combines exact keyword/tag matching with semantic recall across every store, and returns [filename](#<kind>-<id>) links — ALWAYS cite the source so the user can open + verify the original. For 'what do my files/notes say about X', 'find my file/book about Y', 'look up Z in my docs'. Not for live web info (use web_search) and not the habit tracker (use manage_health).",
    'manage_files': "STORE and MANAGE the user's files: STORE a user-attached/uploaded file (add + upload_id — use when the user says 'save/store/remember this image/file/screenshot/document/book'). It ROUTES by type: images/videos → the Gallery (optionally into a named album, e.g. game screenshots into a '<game>' album), PDFs/EPUBs → Books, everything else → the Files store. For Files items you can also replace/correct text (edit), append, set tags (retag), AI-generate tags (autotag), rename, or delete. Identify a Files item by id (from a search_files #file-<id> link) or a unique filename. Every change re-indexes recall. Not for reading (use search_files) or authoring new documents (use the document tools).",
    'manage_gallery': "MANAGE the user's Gallery (photos + videos): tag them, rename them, favorite/unfavorite, hide/unhide, delete, create albums, and FILE media into an album ('sort' game screenshots into a '<game>' album). Use action=list (by album/tag/media_type) to find items + their ids first, then act by id (or a unique name/keyword). To store a NEW chat-attached photo/video, use manage_files add (it routes media into the Gallery). For files/docs use manage_files; for reading book/file contents use search_files.",
    'manage_health': "The user's HABIT TRACKER and health/training log (same data the Health panel shows; backed by the app database — NOT a checklist note and NOT a vault/Obsidian file). Use for the habit tracker and anything health: 'start/add a habit' (create_habit), 'rename a habit / give a habit an emoji or icon / change its category or color' (update_habit), 'delete a habit' (delete_habit), 'mark a habit done' (check_habit), 'list my habits / streaks' (list_habits), habit_heatmap, 'log my lunch ~600 kcal' (log_meal), 'I weigh 72kg' (log_weight), 'log a workout' (log_training), 'set my height/calorie goal' (set_profile), 'calories today', 'weight progress'. Actions: create_habit, update_habit, delete_habit, check_habit, list_habits, habit_heatmap, log_meal, log_weight, log_training, calories, weight_trend, set_profile, summary.",
}

# Reworded descriptions for tools upstream already defines (override-merged so
# upstream's literal string stays untouched).
FORK_DESCRIPTION_OVERRIDES = {
    'write_file': "Write/create or fully rewrite a file ON DISK (source code, configs, project files). Use for new files or full rewrites — NOT create_document (editor panel) and NOT a bash heredoc. NEVER for saving user content/attachments: a user's uploaded file goes through manage_files (action=add with an upload_id — it routes images/videos to the Gallery, PDFs/EPUBs to Books, and everything else to the Files store).",
    'manage_notes': "Create and manage notes and checklists (Google Keep-style). ALWAYS use this for note/todo/checklist/reminder creation — NEVER hit /api/notes via app_api. BUT a recurring HABIT (one with streaks/a heatmap, e.g. 'add a habit', 'track meditation daily', 'rename my habit') is NOT a checklist — use manage_health for the habit tracker, not a note. Accepts natural-language `due_date` like 'tomorrow at 9am' or '11pm today' (parsed in the USER'S timezone). The due_date IS the reminder — it fires a notification at that time, so do NOT also create a calendar event for the same reminder. Set colors, labels, pin, archive. Do NOT use manage_memory for note content.",
    'manage_calendar': "Calendar event management: list, create, update, delete. Each event can carry a tag/category (event_type — work/personal/health/travel/meal/social/admin/other) and importance (low/normal/high/critical). For relative dates like today/tomorrow, prefer passing natural language directly, e.g. dtstart='today 9:00'; the tool resolves it in the user's timezone. ISO datetimes are accepted only when they match the Current date/time context and are in the user's local wall time. Supports all-day events. For event reminders/alarms, pass reminder_minutes; this creates the Notes reminder, so do not also call manage_notes for the same reminder.",
}

# Extra keyword -> tool routing hints (all keys are unique vs upstream).
FORK_KEYWORD_HINTS = {
    frozenset({'add this', 'add this book', 'add to my', 'append to my', 'delete my file', 'edit my file', 'find my', 'fix the text', 'in my files', 'in my notes', 'knowledge', 'knowledge base', 'look up', 'my docs', 'my documents', 'my file', 'my files', 'retag', 'save this', 'search my', 'store this', 'tag my file', 'to my books', 'update my file', 'uploaded file', 'what do my notes say'}): {'manage_files', 'search_files'},
    frozenset({'album', 'create an album', 'hide this photo', 'move to album', 'my gallery', 'my photo', 'my photos', 'my picture', 'my pictures', 'my video', 'my videos', 'rename this photo', 'screenshot', 'screenshots', 'sort my photos', 'sort my pictures', 'tag this photo', 'tag this picture', 'tag this video', 'to my album', 'to my gallery'}): {'manage_files', 'manage_gallery'},
    frozenset({'bmr', 'build a habit', 'calorie', 'calories', 'daily habit', 'exercise', 'habit', 'habit tracker', 'habits', 'health', 'heatmap', 'kcal', 'macros', 'meal', 'nutrition', 'protein', 'streak', 'streaks', 'tdee', 'track a habit', 'training', 'weigh', 'weight', 'workout'}): {'manage_health'},
}
