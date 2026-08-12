# src/tool_schemas_fork.py
"""Fork-only function-tool schemas (manage_health / search_files / manage_files /
manage_gallery / send_ping).

Appended to FUNCTION_TOOL_SCHEMAS by src/tool_schemas.py so upstream's list
literal stays byte-identical. See docs/fork-additive-policy.md.
"""

FORK_FUNCTION_TOOL_SCHEMAS = [   {   'function': {   'description': "Create, log and query the user's health/habits/training "
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
                        'name': 'manage_health',
                        'parameters': {   'properties': {   'action': {   'description': 'What to '
                                                                                         'do.',
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
                                                                          'type': 'string'},
                                                            'activity_level': {   'description': 'sedentary|lightly_active|moderately_active|very_active|extra_active '
                                                                                                 '(set_profile).',
                                                                                  'type': 'string'},
                                                            'cadence': {   'description': 'Habit '
                                                                                          'cadence: '
                                                                                          'daily | '
                                                                                          'weekdays '
                                                                                          '| '
                                                                                          'weekends '
                                                                                          '(create_habit '
                                                                                          'or '
                                                                                          'update_habit). '
                                                                                          'Default '
                                                                                          'daily.',
                                                                           'type': 'string'},
                                                            'carbs_g': {   'description': 'Optional '
                                                                                          'carbohydrate '
                                                                                          'grams '
                                                                                          '(log_meal '
                                                                                          '/ '
                                                                                          'update_meal).',
                                                                           'type': 'number'},
                                                            'category': {   'description': 'Habit '
                                                                                           'category '
                                                                                           '(create_habit '
                                                                                           'or '
                                                                                           'update_habit).',
                                                                            'type': 'string'},
                                                            'color': {   'description': 'Optional '
                                                                                        'accent '
                                                                                        'color for '
                                                                                        'the habit '
                                                                                        '(create_habit '
                                                                                        'or '
                                                                                        'update_habit), '
                                                                                        'e.g. a '
                                                                                        'hex like '
                                                                                        "'#7ec9a3'.",
                                                                         'type': 'string'},
                                                            'daily_kcal_target': {   'description': 'Manual '
                                                                                                    'calorie '
                                                                                                    'target '
                                                                                                    'override '
                                                                                                    '(set_profile).',
                                                                                     'type': 'integer'},
                                                            'date': {   'description': 'YYYY-MM-DD; '
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
                                                                                       'yesterday.',
                                                                        'type': 'string'},
                                                            'date_of_birth': {   'description': 'YYYY-MM-DD '
                                                                                                '(set_profile; '
                                                                                                'for '
                                                                                                'age '
                                                                                                'in '
                                                                                                'TDEE).',
                                                                                 'type': 'string'},
                                                            'days': {   'description': 'Lookback '
                                                                                       'window '
                                                                                       '(weight_trend/habit_heatmap).',
                                                                        'type': 'integer'},
                                                            'description': {   'description': 'Meal '
                                                                                              'description '
                                                                                              '(log_meal '
                                                                                              '/ '
                                                                                              'update_meal).',
                                                                               'type': 'string'},
                                                            'done': {   'description': 'For '
                                                                                       'check_habit: '
                                                                                       'set '
                                                                                       'explicitly, '
                                                                                       'or omit to '
                                                                                       'toggle '
                                                                                       'today.',
                                                                        'type': 'boolean'},
                                                            'duration_min': {   'description': 'Training '
                                                                                               'duration '
                                                                                               'in '
                                                                                               'minutes '
                                                                                               '(log_training).',
                                                                                'type': 'integer'},
                                                            'fat_g': {   'description': 'Optional '
                                                                                        'fat grams '
                                                                                        '(log_meal '
                                                                                        '/ '
                                                                                        'update_meal).',
                                                                         'type': 'number'},
                                                            'habit': {   'description': 'Existing '
                                                                                        'habit '
                                                                                        'name or '
                                                                                        'id '
                                                                                        '(check_habit/habit_heatmap/update_habit/delete_habit).',
                                                                         'type': 'string'},
                                                            'height_cm': {   'description': 'Height '
                                                                                            'in cm '
                                                                                            '(set_profile).',
                                                                             'type': 'number'},
                                                            'icon': {   'description': 'Emoji/icon '
                                                                                       'for the '
                                                                                       'habit '
                                                                                       '(create_habit '
                                                                                       'or '
                                                                                       'update_habit), '
                                                                                       "e.g. '🧘'.",
                                                                        'type': 'string'},
                                                            'kcal': {   'description': 'Calories '
                                                                                       'for the '
                                                                                       'meal '
                                                                                       '(log_meal '
                                                                                       '/ '
                                                                                       'update_meal).',
                                                                        'type': 'integer'},
                                                            'kcal_burned': {   'description': 'Estimated '
                                                                                              'calories '
                                                                                              'burned '
                                                                                              'in '
                                                                                              'the '
                                                                                              'session '
                                                                                              '(log_training).',
                                                                               'type': 'integer'},
                                                            'kg': {   'description': 'Body weight '
                                                                                     'in kilograms '
                                                                                     '(log_weight).',
                                                                      'type': 'number'},
                                                            'kind': {   'description': 'Training '
                                                                                       'type, e.g. '
                                                                                       "'Strength' "
                                                                                       '(log_training).',
                                                                        'type': 'string'},
                                                            'meal_id': {   'description': 'Id of a '
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
                                                                                          '#id.',
                                                                           'type': 'integer'},
                                                            'name': {   'description': 'New habit '
                                                                                       'name '
                                                                                       '(create_habit). '
                                                                                       'For '
                                                                                       'update_habit/delete_habit/check_habit '
                                                                                       'you may '
                                                                                       'instead '
                                                                                       'identify '
                                                                                       'the target '
                                                                                       'via '
                                                                                       "'habit'.",
                                                                        'type': 'string'},
                                                            'new_name': {   'description': 'New '
                                                                                           'name '
                                                                                           'when '
                                                                                           'renaming '
                                                                                           'an '
                                                                                           'existing '
                                                                                           'habit '
                                                                                           '(update_habit).',
                                                                            'type': 'string'},
                                                            'notes': {   'description': 'Optional '
                                                                                        'note '
                                                                                        '(log_meal/log_weight).',
                                                                         'type': 'string'},
                                                            'protein_g': {   'description': 'Optional '
                                                                                            'protein '
                                                                                            'grams '
                                                                                            '(log_meal '
                                                                                            '/ '
                                                                                            'update_meal).',
                                                                             'type': 'number'},
                                                            'rpe': {   'description': 'Rate of '
                                                                                      'perceived '
                                                                                      'exertion '
                                                                                      '1-10 '
                                                                                      '(log_training).',
                                                                       'type': 'integer'},
                                                            'sex': {   'description': 'Biological '
                                                                                      'sex for BMR '
                                                                                      '(set_profile).',
                                                                       'enum': ['M', 'F'],
                                                                       'type': 'string'},
                                                            'sugar_g': {   'description': 'Optional '
                                                                                          'sugar '
                                                                                          'grams '
                                                                                          '(log_meal '
                                                                                          '/ '
                                                                                          'update_meal).',
                                                                           'type': 'number'},
                                                            'summary': {   'description': 'Training '
                                                                                          'notes '
                                                                                          '(log_training).',
                                                                           'type': 'string'},
                                                            'target_kg': {   'description': 'Goal '
                                                                                            'weight '
                                                                                            '(set_profile).',
                                                                             'type': 'number'},
                                                            'target_weekly_loss_kg': {   'description': 'Desired '
                                                                                                        'weekly '
                                                                                                        'loss '
                                                                                                        'for '
                                                                                                        'the '
                                                                                                        'calorie '
                                                                                                        'deficit '
                                                                                                        '(set_profile).',
                                                                                         'type': 'number'}},
                                          'required': ['action'],
                                          'type': 'object'}},
        'type': 'function'},
    {   'function': {   'description': "Search the user's content — their Files (uploaded docs), "
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
                        'name': 'search_files',
                        'parameters': {   'properties': {   'limit': {   'description': 'Max files '
                                                                                        'to return '
                                                                                        '(default '
                                                                                        '12).',
                                                                         'type': 'integer'},
                                                            'query': {   'description': 'What to '
                                                                                        'search '
                                                                                        'for '
                                                                                        '(keywords '
                                                                                        'or a '
                                                                                        'natural-language '
                                                                                        'question).',
                                                                         'type': 'string'},
                                                            'tags': {   'description': 'Optional '
                                                                                       'tag filter '
                                                                                       '(AND-combined).',
                                                                        'items': {'type': 'string'},
                                                                        'type': 'array'}},
                                          'required': ['query'],
                                          'type': 'object'}},
        'type': 'function'},
    {   'function': {   'description': "STORE and MANAGE the user's files. ADD a "
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
                        'name': 'manage_files',
                        'parameters': {   'properties': {   'action': {   'description': 'add = '
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
                                                                                         'file.',
                                                                          'enum': [   'add',
                                                                                      'edit',
                                                                                      'append',
                                                                                      'retag',
                                                                                      'autotag',
                                                                                      'delete'],
                                                                          'type': 'string'},
                                                            'album': {   'description': 'For add '
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
                                                                                        'screenshots.',
                                                                         'type': 'string'},
                                                            'filename': {   'description': 'For '
                                                                                           'add: a '
                                                                                           'friendly '
                                                                                           'name/title '
                                                                                           '(extension '
                                                                                           'kept '
                                                                                           'automatically). '
                                                                                           'For '
                                                                                           'edit: '
                                                                                           'optional '
                                                                                           'rename.',
                                                                            'type': 'string'},
                                                            'id': {   'description': 'The file id '
                                                                                     '(from a '
                                                                                     'search_files '
                                                                                     '#file-<id> '
                                                                                     'link). '
                                                                                     'Preferred '
                                                                                     'for '
                                                                                     'edit/append/retag/autotag/delete '
                                                                                     '(Files '
                                                                                     'items).',
                                                                      'type': 'string'},
                                                            'query': {   'description': 'Alternative '
                                                                                        'to id: a '
                                                                                        'filename '
                                                                                        'or '
                                                                                        'keywords '
                                                                                        'that '
                                                                                        'identify '
                                                                                        'exactly '
                                                                                        'ONE file.',
                                                                         'type': 'string'},
                                                            'tags': {   'description': 'For retag: '
                                                                                       'the tags '
                                                                                       'to set; '
                                                                                       'for add: '
                                                                                       'initial '
                                                                                       'tags.',
                                                                        'items': {'type': 'string'},
                                                                        'type': 'array'},
                                                            'text': {   'description': 'For edit: '
                                                                                       'the new '
                                                                                       'FULL '
                                                                                       'content. '
                                                                                       'For '
                                                                                       'append: '
                                                                                       'the text '
                                                                                       'to add.',
                                                                        'type': 'string'},
                                                            'upload_id': {   'description': 'For '
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
                                                                                            'message).',
                                                                             'type': 'string'}},
                                          'required': ['action'],
                                          'type': 'object'}},
        'type': 'function'},
    {   'function': {   'description': "MANAGE the user's Gallery (photos + videos): tag them, "
                                       'rename them, set/unset favorite, hide/unhide, delete, '
                                       "create albums, and FILE media into an album ('sort'). Use "
                                       'action=list (optionally by album/tag/media_type) to find '
                                       'items and their ids first; identify an item by id (e.g. '
                                       'from a manage_files add result or a list) or a unique '
                                       'name/keyword. To store a NEW chat-attached image/video '
                                       'into the gallery, use manage_files add (it routes media to '
                                       'the Gallery). NOT for documents/files (use manage_files).',
                        'name': 'manage_gallery',
                        'parameters': {   'properties': {   'action': {   'description': 'list = '
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
                                                                                         'delete.',
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
                                                                          'type': 'string'},
                                                            'album': {   'description': 'For move: '
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
                                                                                        'album.',
                                                                         'type': 'string'},
                                                            'id': {   'description': 'The gallery '
                                                                                     'item id '
                                                                                     '(from a list '
                                                                                     'result or a '
                                                                                     'manage_files '
                                                                                     'add). '
                                                                                     'Preferred '
                                                                                     'for item '
                                                                                     'actions.',
                                                                      'type': 'string'},
                                                            'media_type': {   'description': 'For '
                                                                                             'list: '
                                                                                             'restrict '
                                                                                             'to '
                                                                                             'photos '
                                                                                             'or '
                                                                                             'videos.',
                                                                              'enum': [   'image',
                                                                                          'video'],
                                                                              'type': 'string'},
                                                            'name': {   'description': 'For '
                                                                                       'rename: '
                                                                                       'the new '
                                                                                       'label. For '
                                                                                       'create_album/move: '
                                                                                       'the album '
                                                                                       'name.',
                                                                        'type': 'string'},
                                                            'query': {   'description': 'For list: '
                                                                                        'filter by '
                                                                                        'keyword/tag. '
                                                                                        'For item '
                                                                                        'actions '
                                                                                        'without '
                                                                                        'an id: a '
                                                                                        'unique '
                                                                                        'name/keyword '
                                                                                        'identifying '
                                                                                        'ONE item.',
                                                                         'type': 'string'},
                                                            'tags': {   'description': 'For tag: '
                                                                                       'the tags '
                                                                                       'to set '
                                                                                       '(replaces '
                                                                                       'existing).',
                                                                        'items': {'type': 'string'},
                                                                        'type': 'array'}},
                                          'required': ['action'],
                                          'type': 'object'}},
        'type': 'function'},
    {   'function': {   'description': 'Send an immediate ntfy push notification/ping to the user '
                                       'using the configured ntfy integration and reminder topic. '
                                       'Use when the user asks Iris to ping or notify them now. '
                                       'For scheduled reminders, use manage_notes with due_date '
                                       'instead.',
                        'name': 'send_ping',
                        'parameters': {   'properties': {   'message': {   'description': 'Notification '
                                                                                          'body '
                                                                                          'text',
                                                                           'type': 'string'},
                                                            'priority': {   'description': 'Optional '
                                                                                           'ntfy '
                                                                                           'priority '
                                                                                           'such '
                                                                                           'as '
                                                                                           'low, '
                                                                                           'default, '
                                                                                           'high, '
                                                                                           'max, '
                                                                                           'or 1-5',
                                                                            'type': 'string'},
                                                            'tags': {   'description': 'Optional '
                                                                                       'comma-separated '
                                                                                       'ntfy tags',
                                                                        'type': 'string'},
                                                            'title': {   'description': 'Notification '
                                                                                        'title; '
                                                                                        'defaults '
                                                                                        'to Iris',
                                                                         'type': 'string'},
                                                            'topic': {   'description': 'Optional '
                                                                                        'ntfy '
                                                                                        'topic; '
                                                                                        'defaults '
                                                                                        'to the '
                                                                                        'reminder '
                                                                                        'ntfy '
                                                                                        'topic in '
                                                                                        'Settings',
                                                                         'type': 'string'}},
                                          'required': ['message'],
                                          'type': 'object'}},
        'type': 'function'}]
