# src/tool_schemas_fork.py
"""Fork-only function-tool schemas (manage_health / search_files / manage_files /
manage_gallery / send_ping / manage_books).

Appended to FUNCTION_TOOL_SCHEMAS by src/tool_schemas.py so upstream's list
literal stays byte-identical. See docs/fork-additive-policy.md.
"""

FORK_FUNCTION_TOOL_SCHEMAS = [   {   'type': 'function',
        'function': {   'name': 'manage_health',
                        'description': "Create, log and query the user's health/habits/training "
                                       '(same data shown in the Health panel). This IS the habit '
                                       'tracker — NOT a checklist note and NOT a vault file. Use '
                                       "for 'start a meditation habit' (create_habit), 'rename a "
                                       "habit / give it a 🧘 emoji / change its category or color' "
                                       "(update_habit), 'delete a habit' (delete_habit), 'mark "
                                       "meditation done' (check_habit), 'log my lunch ~600 kcal' "
                                       "(log_meal), 'fix that meal to 700 kcal / change its "
                                       "protein' (update_meal — get the meal id from "
                                       "action=calories), 'delete that meal' (delete_meal), 'I "
                                       "weigh 72kg' (log_weight), 'set my height/goal' "
                                       "(set_profile), 'calories today', 'weight progress'. "
                                       'Calories/weight feed the charts; habit check-ins fill the '
                                       "GitHub-style heatmap. To check off a habit that doesn't "
                                       'exist yet, create_habit first.',
                        'parameters': {   'type': 'object',
                                          'properties': {   'action': {   'type': 'string',
                                                                          'enum': [   'create_habit',
                                                                                      'update_habit',
                                                                                      'delete_habit',
                                                                                      'check_habit',
                                                                                      'list_habits',
                                                                                      'habit_heatmap',
                                                                                      'log_meal',
                                                                                      'update_meal',
                                                                                      'delete_meal',
                                                                                      'log_weight',
                                                                                      'log_training',
                                                                                      'calories',
                                                                                      'weight_trend',
                                                                                      'set_profile',
                                                                                      'summary'],
                                                                          'description': 'What to '
                                                                                         'do.'},
                                                            'name': {   'type': 'string',
                                                                        'description': 'New habit '
                                                                                       'name '
                                                                                       '(create_habit). '
                                                                                       'For '
                                                                                       'update_habit/delete_habit/check_habit '
                                                                                       'you may '
                                                                                       'instead '
                                                                                       'identify '
                                                                                       'the target '
                                                                                       'via '
                                                                                       "'habit'."},
                                                            'new_name': {   'type': 'string',
                                                                            'description': 'New '
                                                                                           'name '
                                                                                           'when '
                                                                                           'renaming '
                                                                                           'an '
                                                                                           'existing '
                                                                                           'habit '
                                                                                           '(update_habit).'},
                                                            'icon': {   'type': 'string',
                                                                        'description': 'Emoji/icon '
                                                                                       'for the '
                                                                                       'habit '
                                                                                       '(create_habit '
                                                                                       'or '
                                                                                       'update_habit), '
                                                                                       "e.g. '🧘'."},
                                                            'color': {   'type': 'string',
                                                                         'description': 'Optional '
                                                                                        'accent '
                                                                                        'color for '
                                                                                        'the habit '
                                                                                        '(create_habit '
                                                                                        'or '
                                                                                        'update_habit), '
                                                                                        'e.g. a '
                                                                                        'hex like '
                                                                                        "'#7ec9a3'."},
                                                            'category': {   'type': 'string',
                                                                            'description': 'Habit '
                                                                                           'category '
                                                                                           '(create_habit '
                                                                                           'or '
                                                                                           'update_habit).'},
                                                            'cadence': {   'type': 'string',
                                                                           'description': 'Habit '
                                                                                          'cadence: '
                                                                                          'daily | '
                                                                                          'weekdays '
                                                                                          '| '
                                                                                          'weekends '
                                                                                          '(create_habit '
                                                                                          'or '
                                                                                          'update_habit). '
                                                                                          'Default '
                                                                                          'daily.'},
                                                            'description': {   'type': 'string',
                                                                               'description': 'Meal '
                                                                                              'description '
                                                                                              '(log_meal '
                                                                                              '/ '
                                                                                              'update_meal).'},
                                                            'meal_id': {   'type': 'integer',
                                                                           'description': 'Id of a '
                                                                                          'logged '
                                                                                          'meal to '
                                                                                          'edit or '
                                                                                          'remove '
                                                                                          '(update_meal '
                                                                                          '/ '
                                                                                          'delete_meal). '
                                                                                          'Get it '
                                                                                          'from '
                                                                                          'action=calories, '
                                                                                          'which '
                                                                                          'lists '
                                                                                          'each '
                                                                                          'meal '
                                                                                          'with '
                                                                                          'its '
                                                                                          '#id.'},
                                                            'kcal': {   'type': 'integer',
                                                                        'description': 'Calories '
                                                                                       'for the '
                                                                                       'meal '
                                                                                       '(log_meal '
                                                                                       '/ '
                                                                                       'update_meal).'},
                                                            'protein_g': {   'type': 'number',
                                                                             'description': 'Optional '
                                                                                            'protein '
                                                                                            'grams '
                                                                                            '(log_meal '
                                                                                            '/ '
                                                                                            'update_meal).'},
                                                            'carbs_g': {   'type': 'number',
                                                                           'description': 'Optional '
                                                                                          'carbohydrate '
                                                                                          'grams '
                                                                                          '(log_meal '
                                                                                          '/ '
                                                                                          'update_meal).'},
                                                            'fat_g': {   'type': 'number',
                                                                         'description': 'Optional '
                                                                                        'fat grams '
                                                                                        '(log_meal '
                                                                                        '/ '
                                                                                        'update_meal).'},
                                                            'sugar_g': {   'type': 'number',
                                                                           'description': 'Optional '
                                                                                          'sugar '
                                                                                          'grams '
                                                                                          '(log_meal '
                                                                                          '/ '
                                                                                          'update_meal).'},
                                                            'kg': {   'type': 'number',
                                                                      'description': 'Body weight '
                                                                                     'in kilograms '
                                                                                     '(log_weight).'},
                                                            'habit': {   'type': 'string',
                                                                         'description': 'Existing '
                                                                                        'habit '
                                                                                        'name or '
                                                                                        'id '
                                                                                        '(check_habit/habit_heatmap/update_habit/delete_habit).'},
                                                            'done': {   'type': 'boolean',
                                                                        'description': 'For '
                                                                                       'check_habit: '
                                                                                       'set '
                                                                                       'explicitly, '
                                                                                       'or omit to '
                                                                                       'toggle '
                                                                                       'today.'},
                                                            'kind': {   'type': 'string',
                                                                        'description': 'Training '
                                                                                       'type, e.g. '
                                                                                       "'Strength' "
                                                                                       '(log_training).'},
                                                            'duration_min': {   'type': 'integer',
                                                                                'description': 'Training '
                                                                                               'duration '
                                                                                               'in '
                                                                                               'minutes '
                                                                                               '(log_training).'},
                                                            'rpe': {   'type': 'integer',
                                                                       'description': 'Rate of '
                                                                                      'perceived '
                                                                                      'exertion '
                                                                                      '1-10 '
                                                                                      '(log_training).'},
                                                            'kcal_burned': {   'type': 'integer',
                                                                               'description': 'Estimated '
                                                                                              'calories '
                                                                                              'burned '
                                                                                              'in '
                                                                                              'the '
                                                                                              'session '
                                                                                              '(log_training).'},
                                                            'summary': {   'type': 'string',
                                                                           'description': 'Training '
                                                                                          'notes '
                                                                                          '(log_training).'},
                                                            'height_cm': {   'type': 'number',
                                                                             'description': 'Height '
                                                                                            'in cm '
                                                                                            '(set_profile).'},
                                                            'date_of_birth': {   'type': 'string',
                                                                                 'description': 'YYYY-MM-DD '
                                                                                                '(set_profile; '
                                                                                                'for '
                                                                                                'age '
                                                                                                'in '
                                                                                                'TDEE).'},
                                                            'sex': {   'type': 'string',
                                                                       'enum': ['M', 'F'],
                                                                       'description': 'Biological '
                                                                                      'sex for BMR '
                                                                                      '(set_profile).'},
                                                            'activity_level': {   'type': 'string',
                                                                                  'description': 'sedentary|lightly_active|moderately_active|very_active|extra_active '
                                                                                                 '(set_profile).'},
                                                            'target_kg': {   'type': 'number',
                                                                             'description': 'Goal '
                                                                                            'weight '
                                                                                            '(set_profile).'},
                                                            'target_weekly_loss_kg': {   'type': 'number',
                                                                                         'description': 'Desired '
                                                                                                        'weekly '
                                                                                                        'loss '
                                                                                                        'for '
                                                                                                        'the '
                                                                                                        'calorie '
                                                                                                        'deficit '
                                                                                                        '(set_profile).'},
                                                            'daily_kcal_target': {   'type': 'integer',
                                                                                     'description': 'Manual '
                                                                                                    'calorie '
                                                                                                    'target '
                                                                                                    'override '
                                                                                                    '(set_profile).'},
                                                            'date': {   'type': 'string',
                                                                        'description': 'YYYY-MM-DD; '
                                                                                       'defaults '
                                                                                       'to today '
                                                                                       '(calories/check_habit). '
                                                                                       'Pass '
                                                                                       "yesterday's "
                                                                                       'date to '
                                                                                       'check_habit '
                                                                                       'to mark a '
                                                                                       'habit done '
                                                                                       'for '
                                                                                       'yesterday.'},
                                                            'days': {   'type': 'integer',
                                                                        'description': 'Lookback '
                                                                                       'window '
                                                                                       '(weight_trend/habit_heatmap).'},
                                                            'notes': {   'type': 'string',
                                                                         'description': 'Optional '
                                                                                        'note '
                                                                                        '(log_meal/log_weight).'}},
                                          'required': ['action']}}},
    {   'type': 'function',
        'function': {   'name': 'search_files',
                        'description': "Search the user's content — their Files (uploaded docs), "
                                       'Books (PDF/EPUB), and authored Documents — to recall '
                                       'facts/specs/notes. Combines exact keyword/tag matching '
                                       'with semantic recall across every store. ALWAYS CITE the '
                                       'source in your answer: the tool returns '
                                       '[filename](#<kind>-<id>) links, and the user must be able '
                                       'to open the original to verify — never state a fact from '
                                       'their files without naming the file it came from. Use for '
                                       "'what do my files/notes say about X', 'find my file/book "
                                       "about Y', 'look up Z in my docs'. NOT for live web info "
                                       '(use web_search) and NOT for the habit tracker (use '
                                       'manage_health).',
                        'parameters': {   'type': 'object',
                                          'properties': {   'query': {   'type': 'string',
                                                                         'description': 'What to '
                                                                                        'search '
                                                                                        'for '
                                                                                        '(keywords '
                                                                                        'or a '
                                                                                        'natural-language '
                                                                                        'question).'},
                                                            'tags': {   'type': 'array',
                                                                        'items': {'type': 'string'},
                                                                        'description': 'Optional '
                                                                                       'tag filter '
                                                                                       '(AND-combined).'},
                                                            'limit': {   'type': 'integer',
                                                                         'description': 'Max files '
                                                                                        'to return '
                                                                                        '(default '
                                                                                        '12).'}},
                                          'required': ['query']}}},
    {   'type': 'function',
        'function': {   'name': 'manage_files',
                        'description': "STORE and MANAGE the user's files. ADD a "
                                       'user-attached/uploaded file by its upload_id (from the '
                                       "message's attachment context) — routed by type: "
                                       'images/videos go to the GALLERY (optionally into a named '
                                       "`album`, e.g. game screenshots into a '<game>' album), "
                                       'PDFs/EPUBs go to BOOKS, everything else '
                                       '(docx/xlsx/csv/txt/md/…) to the FILES store. For Files '
                                       'items you can also correct/replace text (edit), append, '
                                       'set or AI-generate tags (retag/autotag), or delete. For '
                                       'edits, find the file with search_files first to get its '
                                       'id. Every change re-indexes recall so search stays in '
                                       'sync. NOT for reading/finding (use search_files), NOT for '
                                       'authoring long new documents (document tools / Library), '
                                       'and NEVER write_file for user files.',
                        'parameters': {   'type': 'object',
                                          'properties': {   'action': {   'type': 'string',
                                                                          'enum': [   'add',
                                                                                      'edit',
                                                                                      'append',
                                                                                      'retag',
                                                                                      'autotag',
                                                                                      'delete'],
                                                                          'description': 'add = '
                                                                                         'store an '
                                                                                         'uploaded/attached '
                                                                                         'file '
                                                                                         '(requires '
                                                                                         'upload_id; '
                                                                                         'routed '
                                                                                         'to '
                                                                                         'Gallery/Books/Files '
                                                                                         'by '
                                                                                         'type); '
                                                                                         'edit = '
                                                                                         'replace '
                                                                                         'a Files '
                                                                                         "item's "
                                                                                         'full '
                                                                                         'text; '
                                                                                         'append = '
                                                                                         'add '
                                                                                         'text; '
                                                                                         'retag = '
                                                                                         'set user '
                                                                                         'tags; '
                                                                                         'autotag '
                                                                                         '= '
                                                                                         'AI-generate '
                                                                                         'tags; '
                                                                                         'delete = '
                                                                                         'remove '
                                                                                         'the '
                                                                                         'file.'},
                                                            'upload_id': {   'type': 'string',
                                                                             'description': 'For '
                                                                                            'add: '
                                                                                            'the '
                                                                                            'attachment/upload '
                                                                                            'id '
                                                                                            '(listed '
                                                                                            'in '
                                                                                            'the '
                                                                                            '[user '
                                                                                            'attachments] '
                                                                                            'context '
                                                                                            'of '
                                                                                            'the '
                                                                                            "user's "
                                                                                            'message).'},
                                                            'album': {   'type': 'string',
                                                                         'description': 'For add '
                                                                                        'of an '
                                                                                        'image/video: '
                                                                                        'the '
                                                                                        'Gallery '
                                                                                        'album '
                                                                                        'name to '
                                                                                        'file it '
                                                                                        'under '
                                                                                        '(created '
                                                                                        'if it '
                                                                                        "doesn't "
                                                                                        'exist), '
                                                                                        'e.g. a '
                                                                                        'game name '
                                                                                        'for '
                                                                                        'screenshots.'},
                                                            'id': {   'type': 'string',
                                                                      'description': 'The file id '
                                                                                     '(from a '
                                                                                     'search_files '
                                                                                     '#file-<id> '
                                                                                     'link). '
                                                                                     'Preferred '
                                                                                     'for '
                                                                                     'edit/append/retag/autotag/delete '
                                                                                     '(Files '
                                                                                     'items).'},
                                                            'query': {   'type': 'string',
                                                                         'description': 'Alternative '
                                                                                        'to id: a '
                                                                                        'filename '
                                                                                        'or '
                                                                                        'keywords '
                                                                                        'that '
                                                                                        'identify '
                                                                                        'exactly '
                                                                                        'ONE '
                                                                                        'file.'},
                                                            'text': {   'type': 'string',
                                                                        'description': 'For edit: '
                                                                                       'the new '
                                                                                       'FULL '
                                                                                       'content. '
                                                                                       'For '
                                                                                       'append: '
                                                                                       'the text '
                                                                                       'to add.'},
                                                            'tags': {   'type': 'array',
                                                                        'items': {'type': 'string'},
                                                                        'description': 'For retag: '
                                                                                       'the tags '
                                                                                       'to set; '
                                                                                       'for add: '
                                                                                       'initial '
                                                                                       'tags.'},
                                                            'filename': {   'type': 'string',
                                                                            'description': 'For '
                                                                                           'add: a '
                                                                                           'friendly '
                                                                                           'name/title '
                                                                                           '(extension '
                                                                                           'kept '
                                                                                           'automatically). '
                                                                                           'For '
                                                                                           'edit: '
                                                                                           'optional '
                                                                                           'rename.'}},
                                          'required': ['action']}}},
    {   'type': 'function',
        'function': {   'name': 'manage_gallery',
                        'description': "MANAGE the user's Gallery (photos + videos): tag them, "
                                       'rename them, set/unset favorite, hide/unhide, delete, '
                                       "create albums, and FILE media into an album ('sort'). Use "
                                       'action=list (optionally by album/tag/media_type) to find '
                                       'items and their ids first; identify an item by id (e.g. '
                                       'from a manage_files add result or a list) or a unique '
                                       'name/keyword. To store a NEW chat-attached image/video '
                                       'into the gallery, use manage_files add (it routes media to '
                                       'the Gallery). NOT for documents/files (use manage_files).',
                        'parameters': {   'type': 'object',
                                          'properties': {   'action': {   'type': 'string',
                                                                          'enum': [   'list',
                                                                                      'tag',
                                                                                      'rename',
                                                                                      'create_album',
                                                                                      'move',
                                                                                      'favorite',
                                                                                      'unfavorite',
                                                                                      'hide',
                                                                                      'unhide',
                                                                                      'delete'],
                                                                          'description': 'list = '
                                                                                         'find '
                                                                                         'photos/videos '
                                                                                         '(by '
                                                                                         'album/tag/media_type); '
                                                                                         'tag = '
                                                                                         'set '
                                                                                         'tags; '
                                                                                         'rename = '
                                                                                         'set the '
                                                                                         'label; '
                                                                                         'create_album '
                                                                                         '= make '
                                                                                         'an '
                                                                                         'album; '
                                                                                         'move = '
                                                                                         'file an '
                                                                                         'item '
                                                                                         'into an '
                                                                                         'album '
                                                                                         '(created '
                                                                                         'if '
                                                                                         'needed); '
                                                                                         'favorite/unfavorite; '
                                                                                         'hide/unhide; '
                                                                                         'delete.'},
                                                            'id': {   'type': 'string',
                                                                      'description': 'The gallery '
                                                                                     'item id '
                                                                                     '(from a list '
                                                                                     'result or a '
                                                                                     'manage_files '
                                                                                     'add). '
                                                                                     'Preferred '
                                                                                     'for item '
                                                                                     'actions.'},
                                                            'query': {   'type': 'string',
                                                                         'description': 'For list: '
                                                                                        'filter by '
                                                                                        'keyword/tag. '
                                                                                        'For item '
                                                                                        'actions '
                                                                                        'without '
                                                                                        'an id: a '
                                                                                        'unique '
                                                                                        'name/keyword '
                                                                                        'identifying '
                                                                                        'ONE '
                                                                                        'item.'},
                                                            'name': {   'type': 'string',
                                                                        'description': 'For '
                                                                                       'rename: '
                                                                                       'the new '
                                                                                       'label. For '
                                                                                       'create_album/move: '
                                                                                       'the album '
                                                                                       'name.'},
                                                            'album': {   'type': 'string',
                                                                         'description': 'For move: '
                                                                                        'the album '
                                                                                        'to file '
                                                                                        'the item '
                                                                                        'into '
                                                                                        '(created '
                                                                                        'if it '
                                                                                        "doesn't "
                                                                                        'exist). '
                                                                                        'For list: '
                                                                                        'filter to '
                                                                                        'this '
                                                                                        'album.'},
                                                            'media_type': {   'type': 'string',
                                                                              'enum': [   'image',
                                                                                          'video'],
                                                                              'description': 'For '
                                                                                             'list: '
                                                                                             'restrict '
                                                                                             'to '
                                                                                             'photos '
                                                                                             'or '
                                                                                             'videos.'},
                                                            'tags': {   'type': 'array',
                                                                        'items': {'type': 'string'},
                                                                        'description': 'For tag: '
                                                                                       'the tags '
                                                                                       'to set '
                                                                                       '(replaces '
                                                                                       'existing).'}},
                                          'required': ['action']}}},
    {   'type': 'function',
        'function': {   'name': 'send_ping',
                        'description': 'Send an immediate ntfy push notification/ping to the user '
                                       'using the configured ntfy integration and reminder topic. '
                                       'Use when the user asks Iris to ping or notify them now. '
                                       'For scheduled reminders, use manage_notes with due_date '
                                       'instead.',
                        'parameters': {   'type': 'object',
                                          'properties': {   'message': {   'type': 'string',
                                                                           'description': 'Notification '
                                                                                          'body '
                                                                                          'text'},
                                                            'title': {   'type': 'string',
                                                                         'description': 'Notification '
                                                                                        'title; '
                                                                                        'defaults '
                                                                                        'to Iris'},
                                                            'topic': {   'type': 'string',
                                                                         'description': 'Optional '
                                                                                        'ntfy '
                                                                                        'topic; '
                                                                                        'defaults '
                                                                                        'to the '
                                                                                        'reminder '
                                                                                        'ntfy '
                                                                                        'topic in '
                                                                                        'Settings'},
                                                            'priority': {   'type': 'string',
                                                                            'description': 'Optional '
                                                                                           'ntfy '
                                                                                           'priority '
                                                                                           'such '
                                                                                           'as '
                                                                                           'low, '
                                                                                           'default, '
                                                                                           'high, '
                                                                                           'max, '
                                                                                           'or '
                                                                                           '1-5'},
                                                            'tags': {   'type': 'string',
                                                                        'description': 'Optional '
                                                                                       'comma-separated '
                                                                                       'ntfy '
                                                                                       'tags'}},
                                          'required': ['message']}}},
    {   'type': 'function',
        'function': {   'name': 'manage_books',
                        'description': "List/read the user's EPUB and PDF books (PDF/EPUB files in "
                                       'their Knowledge base) and save reading progress. Use this '
                                       'when the user asks about books, EPUBs, PDFs, reading '
                                       'status, or what they have read.',
                        'parameters': {   'type': 'object',
                                          'properties': {   'action': {   'type': 'string',
                                                                          'enum': [   'list',
                                                                                      'read',
                                                                                      'progress'],
                                                                          'description': 'Action '
                                                                                         'to '
                                                                                         'perform'},
                                                            'query': {   'type': 'string',
                                                                         'description': 'Search/list '
                                                                                        'query'},
                                                            'limit': {   'type': 'integer',
                                                                         'description': 'Maximum '
                                                                                        'list '
                                                                                        'results'},
                                                            'path': {   'type': 'string',
                                                                        'description': 'Vault-relative '
                                                                                       'EPUB/PDF '
                                                                                       'path'},
                                                            'chapter_index': {   'type': 'integer',
                                                                                 'description': 'EPUB '
                                                                                                'chapter '
                                                                                                'index '
                                                                                                'or '
                                                                                                'PDF '
                                                                                                'page '
                                                                                                'index, '
                                                                                                'zero-based'},
                                                            'page_index': {   'type': 'integer',
                                                                              'description': 'Alias '
                                                                                             'for '
                                                                                             'chapter_index '
                                                                                             'when '
                                                                                             'reading '
                                                                                             'PDFs'},
                                                            'scroll_percent': {   'type': 'number',
                                                                                  'description': 'Progress '
                                                                                                 'within '
                                                                                                 'the '
                                                                                                 'current '
                                                                                                 'chapter/page'},
                                                            'chapter_title': {   'type': 'string',
                                                                                 'description': 'Current '
                                                                                                'chapter/page '
                                                                                                'title'},
                                                            'title': {   'type': 'string',
                                                                         'description': 'Book '
                                                                                        'title for '
                                                                                        'progress '
                                                                                        'notes'},
                                                            'author': {   'type': 'string',
                                                                          'description': 'Book '
                                                                                         'author '
                                                                                         'for '
                                                                                         'progress '
                                                                                         'notes'},
                                                            'kind': {   'type': 'string',
                                                                        'description': 'Book type, '
                                                                                       'epub or '
                                                                                       'pdf'}},
                                          'required': ['action']}}}]
