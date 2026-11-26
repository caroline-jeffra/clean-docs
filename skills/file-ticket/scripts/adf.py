"""Build a Jira description from a ticket spec, enforcing the profile's template for its type.

Usage: python3 adf.py SPEC.json OUT_DIR --type TYPE --project KEY [--projects ABC,XYZ]
                      [--update KEY-123] [--profile NAME] [--repo PATH]

The house style comes from the clean-docs profile for the repo (scripts/house_style.py
at the plugin root; --profile picks one by name). From it this script takes the
Jira site, the projects always linked, the summary limit, the ticket types, each
type's template and the urgency levels.

--update builds the description for an existing ticket (reference/update.md): the
first line says it was restructured, and workitem.json is in the shape of
`acli jira workitem edit --from-json`, which has no custom fields.

SPEC.json is {"summary": "...", "blocks": [...], "fields": {...}}, each block a one-key object:
  {"h": "Header"}            bold paragraph
  {"p": "Text"}              paragraph
  {"ul": ["item", ...]}      bullet list
  {"ol": ["item", ...]}      ordered list

The optional "fields" sets components and single-select custom fields:
  {"customfield_10000": {"value": "Option"}, "components": [{"name": "Name"}]}

In any text, `backticks` become inline code and ticket keys become links to the
Jira browse page. Keys of the profile's link_projects, --project and any
--projects are linked.

A template is the type's Markdown file, whose table rows read
  | <n> | <Header> | <Block> | <yes|no> | <content> |
where Block is the allowed block kinds joined by "/" (p, ul, ol), or "files" for
the impacted-files section, and the fourth column says whether the section needs
content. The script refuses a spec unless:
  - the summary fits the profile's max_summary;
  - the first block is the generated-ticket line, with an urgency level from the
    profile's rubric when the type has urgency. The level is written plain in the
    spec and rendered bold in both outputs;
  - the headers are exactly the template's, in order, each section holding only
    its allowed block kinds;
  - every required section has content;
  - code spans appear only in the files section, which is a bullet list of `path`
    items or the not-investigated line, optionally followed by an Overlaps line.

Writes OUT_DIR/description.md (markdown for the Atlassian MCP's createJiraIssue
and editJiraIssue, which convert it to ADF), and for the acli fallback
OUT_DIR/workitem.json (for `acli jira workitem create --from-json`, so no ticket
text passes through the shell) and OUT_DIR/description.json (the ADF alone, for
`acli jira workitem edit --description-file`). It prints the keys to link and
the fields as JSON, to pass verbatim to createJiraIssue's additional_fields.
"""

import argparse
import json
import os
import re
import sys
from collections import namedtuple

PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(PLUGIN, 'scripts'))

import house_style  # noqa: E402

GENERATED = 'This is a generated ticket description.'
UPDATED = 'This ticket description was restructured by a generator from the original.'
URGENCY_SUFFIX = ' Assessed ticket urgency: '
URGENCY = GENERATED + URGENCY_SUFFIX
NOT_INVESTIGATED = 'Codebase not yet investigated.'
CODE_SPAN = r'`[^`]+`'
OVERLAPS = r'Overlaps with [A-Z][A-Z0-9]{1,9}-\d+(?:, [A-Z][A-Z0-9]{1,9}-\d+)*\.'
PROJECT_KEY = r'[A-Z][A-Z0-9]{1,9}'
BLOCK_KINDS = ('p', 'ul', 'ol')
MD_SPECIAL = re.compile(r'([\\`*_\[\]<>~|#!])')
MD_BLOCK_START = re.compile(r'^(\s*)([-+>]|\d+[.)])')
ROW = re.compile(r'^\| *\d+ *\| *(.+?) *\| *([a-z/]+) *\| *(yes|no) *\|', re.M)
LEVEL = re.compile(r'^## \d+\. (.+?) — ', re.M)

Section = namedtuple('Section', 'header kinds required')
# sections: [Section]; files: the files section's header or None; levels: urgency levels, () for none
Template = namedtuple('Template', 'sections files levels')


def fail(msg):
    sys.exit('spec error: ' + msg)


# ---------------------------------------------------------------- templates


def parse_template(text, where='template'):
    """The sections of a template file's table, in order."""
    sections = []
    for header, block, required in ROW.findall(text):
        kinds = tuple(block.split('/'))
        if kinds != ('files',) and not all(k in BLOCK_KINDS for k in kinds):
            sys.exit(f'{where}: section "{header}" has block "{block}"; use p, ul, ol joined by "/", or files')
        sections.append(Section(header, kinds, required == 'yes'))
    if not sections:
        sys.exit(f'{where}: no section table found (rows like "| 1 | Header | p | yes | ... |")')
    if len({s.header for s in sections}) != len(sections):
        sys.exit(f'{where}: section headers must be unique')
    if sum(s.kinds == ('files',) for s in sections) > 1:
        sys.exit(f'{where}: at most one files section')
    return sections


def parse_levels(text, where='urgency rubric'):
    levels = tuple(LEVEL.findall(text))
    if not levels:
        sys.exit(f'{where}: no levels found (headings like "## 1. Critical — meaning")')
    return levels


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def template_for(profile, ticket_type):
    spec = profile['tickets']['types'][ticket_type]
    sections = parse_template(read(spec['template']), spec['template'])
    files = next((s.header for s in sections if s.kinds == ('files',)), None)
    levels = ()
    if spec.get('urgency', True):
        rubric = profile['tickets'].get('urgency')
        if not rubric:
            sys.exit(f'type "{ticket_type}" has urgency but the profile names no urgency rubric')
        levels = parse_levels(read(rubric), rubric)
    return Template(sections, files, levels)


# ---------------------------------------------------------------- inline


def key_pattern(projects):
    return r'\b(?:' + '|'.join(map(re.escape, projects)) + r')-\d+\b'


def make_inline(projects, browse):
    key = key_pattern(projects)

    def inline(s):
        out = []
        for kind, text in split_inline(s, key):
            if kind == 'key':
                out.append(
                    {'type': 'text', 'text': text, 'marks': [{'type': 'link', 'attrs': {'href': browse + text}}]}
                )
            elif kind == 'code':
                out.append({'type': 'text', 'text': text, 'marks': [{'type': 'code'}]})
            else:
                out.append({'type': 'text', 'text': text})
        return out

    return inline


def split_inline(s, key):
    """Yield (kind, text) parts of s, kind one of 'key', 'code' or 'text'."""
    for part in re.split(rf'({key}|{CODE_SPAN})', s):
        if not part:
            continue
        if re.fullmatch(key, part):
            yield 'key', part
        elif re.fullmatch(CODE_SPAN, part):
            yield 'code', part[1:-1]
        else:
            yield 'text', part


# ---------------------------------------------------------------- checks


def check_block(i, block):
    if not isinstance(block, dict) or len(block) != 1:
        fail(f'block {i} must be a one-key object, got {block!r}')
    ((kind, value),) = block.items()
    if kind in ('h', 'p'):
        if not isinstance(value, str) or not value.strip():
            fail(f'block {i}: "{kind}" needs non-empty text')
        return kind, [value]
    if kind in ('ul', 'ol'):
        if not isinstance(value, list) or not value:
            fail(f'block {i}: "{kind}" needs a non-empty list of strings; omit the block for an empty optional section')
        if not all(isinstance(v, str) and v.strip() for v in value):
            fail(f'block {i}: every "{kind}" item must be non-empty text')
        return kind, value
    fail(f'block {i}: unknown kind {kind!r}')


def check_template(blocks, template, update=False):
    first = blocks[0].get('p', '') if blocks else ''
    line = UPDATED if update else GENERATED
    urgency = line + URGENCY_SUFFIX
    if not template.levels:
        if first != line:
            fail(f'first block must be {{"p": "{line}"}}')
    elif not (first.startswith(urgency) and first[len(urgency) :] in template.levels):
        fail(f'first block must be {{"p": "{urgency}<{" | ".join(template.levels)}>"}}')

    expected = [s.header for s in template.sections]
    headers = [b['h'] for b in blocks if 'h' in b]
    if headers != expected:
        fail(f'headers must be exactly, in order: {expected}')
    if 'h' not in blocks[1]:
        fail('the first header must follow the first line directly')

    by_header = {s.header: s for s in template.sections}
    section, content = None, {}
    for i, block in enumerate(blocks[1:], 1):
        ((kind, value),) = block.items()
        if kind == 'h':
            section = value
            content[section] = []
            continue
        content[section].append(block)
        if section != template.files and kind not in by_header[section].kinds:
            fail(f'block {i}: "{section}" takes {"/".join(by_header[section].kinds)}, not {kind}')
        texts = [value] if isinstance(value, str) else value
        if section != template.files and any(re.search(CODE_SPAN, t) for t in texts):
            where = f'only in "{template.files}"' if template.files else 'nowhere in this type'
            fail(f'block {i}: code spans belong {where}, not in "{section}"')
    empty = [s.header for s in template.sections if s.required and not content[s.header]]
    if empty:
        fail(f'sections need content: {empty}')
    if template.files and content[template.files]:
        check_files(template.files, content[template.files])


def check_files(header, blocks):
    """The files section is a list of `path` items (or the not-investigated line), then an optional Overlaps line."""
    first, rest = blocks[0], blocks[1:]
    paths = first.get('ul')
    if not (first == {'p': NOT_INVESTIGATED} or (paths and all(re.fullmatch(CODE_SPAN, i) for i in paths))):
        fail(f'"{header}" must start with a bullet list of `path` items, or {{"p": "{NOT_INVESTIGATED}"}}')
    if rest and (len(rest) > 1 or not re.fullmatch(OVERLAPS, rest[0].get('p', ''))):
        fail(f'"{header}" may end only with one {{"p": "Overlaps with KEY-1, KEY-2."}}')


def check_fields(fields):
    """Fields must be single-select custom fields ({"value": ...}) or components ([{"name": ...}])."""
    if not isinstance(fields, dict):
        fail('"fields" must be an object')

    def named(v, key):
        return isinstance(v, dict) and list(v) == [key] and isinstance(v[key], str) and v[key].strip()

    for field, value in fields.items():
        if field == 'components':
            if not isinstance(value, list) or not value or not all(named(c, 'name') for c in value):
                fail('"components" must be a non-empty list of {"name": "..."}')
        elif re.fullmatch(r'customfield_\d+', field):
            if not named(value, 'value'):
                fail(f'"{field}" must be {{"value": "..."}}')
        else:
            fail(f'unknown field {field!r}: only components and customfield_* selects are set')


# ---------------------------------------------------------------- output


def build(blocks, projects, template, browse, update=False):
    for i, block in enumerate(blocks):
        check_block(i, block)
    check_template(blocks, template, update)

    inline = make_inline(projects, browse)

    def h(t):
        return {
            'type': 'paragraph',
            'content': [dict(n, marks=n.get('marks', []) + [{'type': 'strong'}]) for n in inline(t)],
        }

    def p(t):
        return {'type': 'paragraph', 'content': inline(t)}

    def ul(items):
        return {'type': 'bulletList', 'content': [{'type': 'listItem', 'content': [p(i)]} for i in items]}

    def ol(items):
        return {'type': 'orderedList', 'content': [{'type': 'listItem', 'content': [p(i)]} for i in items]}

    kinds = {'h': h, 'p': p, 'ul': ul, 'ol': ol}
    content = [kinds[k](v) for b in blocks for k, v in b.items()]
    line, level = split_urgency(blocks[0].get('p', '') if blocks else '')
    if level:
        content[0] = {
            'type': 'paragraph',
            'content': [*inline(line), {'type': 'text', 'text': level, 'marks': [{'type': 'strong'}]}],
        }
    return {'version': 1, 'type': 'doc', 'content': content}


def split_urgency(first):
    """Split an already-checked first line into (text up to the level, level); level is '' without urgency."""
    head, sep, level = first.rpartition(URGENCY_SUFFIX)
    return (head + sep, level) if sep else (first, '')


def markdown(blocks, projects, browse):
    """Render already-checked blocks as markdown; plain text is escaped so it stays literal."""
    key = key_pattern(projects)

    def inline(s):
        out = []
        for kind, text in split_inline(s, key):
            if kind == 'key':
                out.append(f'[{text}]({browse}{text})')
            elif kind == 'code':
                out.append(f'`{text}`')
            else:
                out.append(MD_SPECIAL.sub(r'\\\1', ' '.join(text.split('\n'))))
        return MD_BLOCK_START.sub(lambda m: m.group(1) + m.group(2)[:-1] + '\\' + m.group(2)[-1], ''.join(out), count=1)

    kinds = {
        'h': lambda t: f'**{inline(t)}**',
        'p': inline,
        'ul': lambda items: '\n'.join(f'- {inline(i)}' for i in items),
        'ol': lambda items: '\n'.join(f'{n}. {inline(i)}' for n, i in enumerate(items, 1)),
    }
    rendered = [kinds[k](v) for b in blocks for k, v in b.items()]
    line, level = split_urgency(blocks[0].get('p', '') if blocks else '')
    if level:
        rendered[0] = f'{inline(line)}**{level}**'
    return '\n\n'.join(rendered) + '\n'


def load_profile(repo, name):
    try:
        profile = house_style.resolve(repo, name)
    except house_style.ProfileError as e:
        sys.exit(f'profile error: {e}')
    resolved = profile['resolved']
    if resolved['from'] == 'ambiguous':
        sys.exit(f'profile error: several profiles match this repo equally: {resolved["candidates"]}; pass --profile')
    tracker = profile['tracker']
    if tracker['kind'] != 'jira':
        sys.exit(f'profile error: {resolved["name"]} files tickets in "{tracker["kind"]}", and file-ticket needs jira')
    return profile


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('spec')
    ap.add_argument('out_dir')
    ap.add_argument('--type', required=True, type=str.lower)
    ap.add_argument('--project', required=True, type=str.upper)
    ap.add_argument('--projects', default='', help='extra project keys to link, comma-separated')
    ap.add_argument('--update', metavar='KEY', type=str.upper, help='existing ticket whose description this replaces')
    ap.add_argument('--profile', help='profile name (default: the one matching --repo)')
    ap.add_argument('--repo', default='.', help='repo to match a profile for (default: %(default)s)')
    args = ap.parse_args()

    profile = load_profile(args.repo, args.profile)
    types = profile['tickets']['types']
    if args.type not in types:
        sys.exit(f"--type must be one of the profile's types: {sorted(types)}")
    if not re.fullmatch(PROJECT_KEY, args.project):
        sys.exit('--project must be a Jira project key such as ABC')
    extra = [k.strip().upper() for k in args.projects.split(',') if k.strip()]
    bad = [k for k in extra if not re.fullmatch(PROJECT_KEY, k)]
    if bad:
        sys.exit(f'--projects takes project keys such as ABC, not {bad}')
    if args.update and not re.fullmatch(rf'{re.escape(args.project)}-\d+', args.update):
        sys.exit('--update takes a ticket key in --project, such as ABC-123')
    browse = profile['tracker']['site'].rstrip('/') + '/browse/'
    projects = list(dict.fromkeys(profile['tracker']['link_projects'] + [args.project] + extra))
    template = template_for(profile, args.type)
    max_summary = profile['tickets']['max_summary']

    with open(args.spec, encoding='utf-8-sig') as f:
        spec = json.load(f)
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get('summary'), str)
        or not isinstance(spec.get('blocks'), list)
    ):
        fail('top level must be {"summary": "...", "blocks": [...]}')
    fields = spec.get('fields', {})
    check_fields(fields)
    summary = spec['summary'].strip()
    if not summary:
        fail('summary is empty')
    if len(summary) > max_summary:
        fail(f'summary is {len(summary)} characters; the limit is {max_summary}')

    description = build(spec['blocks'], projects, template, browse, bool(args.update))
    if args.update:
        workitem = {'issues': [args.update], 'summary': summary, 'description': description}
    else:
        workitem = {
            'projectKey': args.project,
            'type': types[args.type]['name'],
            'summary': summary,
            'description': description,
        }
        if fields:
            workitem['additionalAttributes'] = fields
    for name, data in (('workitem.json', workitem), ('description.json', description)):
        with open(os.path.join(args.out_dir, name), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    with open(os.path.join(args.out_dir, 'description.md'), 'w', encoding='utf-8') as f:
        f.write(markdown(spec['blocks'], projects, browse))
    linkable = set(re.findall(key_pattern(projects), json.dumps(spec['blocks']))) - {args.update}
    print(f'profile: {profile["resolved"]["name"]} ({profile["resolved"]["from"]})')
    print(f'linkable keys: {sorted(linkable)}')
    print(f'fields: {json.dumps(fields)}')


if __name__ == '__main__':
    main()
