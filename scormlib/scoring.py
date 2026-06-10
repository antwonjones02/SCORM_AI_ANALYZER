"""Deterministic AI-readiness scoring (0-100) with an itemized breakdown.

This is the baseline score computed WITHOUT any LLM: it measures whether an
AI system (search, recommendations, skills inference) could index this course
from its package data alone."""

GENERIC_TITLES = {'untitled', 'course', 'new course', 'module', 'title',
                  'unknown', 'index', 'project'}


def readiness_score(metadata, organizations, content, inventory=None):
    """Compute the deterministic AI-readiness score.

    Returns (score:int 0-100, breakdown:dict of criterion -> {points, max, note}).
    """
    breakdown = {}

    def add(name, points, max_points, note):
        breakdown[name] = {'points': points, 'max': max_points, 'note': note}

    # Title (10)
    title = (metadata.get('title') or '').strip()
    generic = (title.lower() in GENERIC_TITLES
               or title.lower().startswith(('untitled', 'new course', 'new project')))
    if title and not generic and len(title) >= 4:
        add('title', 10, 10, f'Meaningful title present: "{title[:60]}"')
    elif title:
        add('title', 4, 10, f'Title present but generic: "{title[:60]}"')
    else:
        add('title', 0, 10, 'No title found')

    # Description (15)
    desc = (metadata.get('description') or '').strip()
    if len(desc) >= 80:
        add('description', 15, 15, f'Rich description ({len(desc)} chars)')
    elif len(desc) >= 25:
        add('description', 9, 15, f'Short description ({len(desc)} chars)')
    elif desc:
        add('description', 4, 15, 'Minimal description')
    else:
        add('description', 0, 15, 'No description — AI cannot summarize without content mining')

    # Keywords / skills tags (10)
    keywords = metadata.get('keywords') or []
    if len(keywords) >= 3:
        add('keywords', 10, 10, f'{len(keywords)} keywords tagged')
    elif keywords:
        add('keywords', 5, 10, f'Only {len(keywords)} keyword(s)')
    else:
        add('keywords', 0, 10, 'No keywords/skills tags')

    # Duration (5)
    if metadata.get('duration_minutes'):
        add('duration', 5, 5,
            f'Duration metadata set ({metadata["duration_minutes"]} min)')
    elif metadata.get('duration'):
        add('duration', 3, 5, f'Duration present but unparseable: {metadata["duration"]}')
    else:
        add('duration', 0, 5, 'No duration metadata')

    # Learning objectives (15)
    objective_count = 0
    for org in organizations or []:
        stack = list(org.get('items', []))
        while stack:
            item = stack.pop()
            objective_count += len([o for o in item.get('objectives', [])
                                    if o.get('description') or o.get('id')])
            stack.extend(item.get('children', []))
    if objective_count >= 2:
        add('objectives', 10, 10, f'{objective_count} objectives defined in manifest')
    elif objective_count == 1:
        add('objectives', 5, 10, '1 objective defined')
    else:
        add('objectives', 0, 10, 'No learning objectives in manifest')

    # Structure (10): multiple titled items
    titled_items = 0
    for org in organizations or []:
        stack = list(org.get('items', []))
        while stack:
            item = stack.pop()
            if (item.get('title') or '').strip():
                titled_items += 1
            stack.extend(item.get('children', []))
    if titled_items >= 3:
        add('structure', 10, 10, f'{titled_items} titled modules/items')
    elif titled_items >= 1:
        add('structure', 5, 10, f'Only {titled_items} titled item(s)')
    else:
        add('structure', 0, 10, 'No titled structure')

    # Extractable text (15)
    word_count = (content or {}).get('word_count', 0)
    if word_count >= 500:
        add('extractable_text', 15, 15, f'{word_count} words of extractable text')
    elif word_count >= 150:
        add('extractable_text', 10, 15, f'{word_count} words of extractable text')
    elif word_count >= 30:
        add('extractable_text', 5, 15, f'Only {word_count} words extractable')
    else:
        add('extractable_text', 0, 15,
            'No readable text — content is a black box (likely media/canvas only)')

    # Assessment present (10)
    if (content or {}).get('quiz_questions'):
        n = len(content['quiz_questions'])
        add('assessment', 10, 10, f'{n} quiz question(s) detected')
    else:
        mastery = any(
            item.get('mastery_score')
            for org in (organizations or [])
            for item in _walk(org.get('items', [])))
        if mastery:
            add('assessment', 6, 10, 'Mastery score set but no questions extracted')
        else:
            add('assessment', 0, 10, 'No assessment detected')

    # Language (5)
    if metadata.get('language'):
        add('language', 5, 5, f'Language: {metadata["language"]}')
    else:
        add('language', 0, 5, 'No language metadata')

    # Categorization (5): classification taxonomy or categories
    if metadata.get('categories') or metadata.get('classifications'):
        add('categorization', 5, 5, 'Classification/taxonomy present')
    else:
        add('categorization', 0, 5, 'No category/taxonomy classification')

    # Author / provenance (5)
    if metadata.get('author') or metadata.get('contributors'):
        add('provenance', 5, 5, 'Author/contributor metadata present')
    else:
        add('provenance', 0, 5, 'No author metadata')

    score = sum(v['points'] for v in breakdown.values())
    max_total = sum(v['max'] for v in breakdown.values())
    # Normalize defensively in case rubric weights change
    score = round(100 * score / max_total) if max_total else 0
    return score, breakdown


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item.get('children', []))


def score_to_tier(score):
    if score is None:
        return 'Unknown'
    if score >= 70:
        return 'AI Ready'
    if score >= 40:
        return 'Partially Ready'
    return 'Not Ready'
