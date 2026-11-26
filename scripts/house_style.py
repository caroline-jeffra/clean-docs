"""Resolve and load clean-docs house-style profiles.

A profile is a folder named in PascalCase holding a profile.json and the Markdown
templates it names. Profiles live outside this plugin, in a profiles directory:

  $CLEAN_DOCS_PROFILES, else "profiles_dir" in
  $XDG_CONFIG_HOME/clean-docs/config.json (default ~/.config/clean-docs/config.json).

The plugin ships one profile, Default, under profiles/Default. Every other profile
is laid over it: objects merge key by key, anything else replaces, and a null
removes the key. Null is allowed only where removing makes sense: a ticket type
("types": {"task": null}), tickets.fields, tickets.urgency, on_ship.comment,
on_ship.transition, tracker.project and tracker.repo. A template named in a profile
is looked up in that profile's folder first, then in Default.

A profile may hold named "overrides" for single repos: {"Gadget": {"match": {...},
"tracker": {"project": "GDG"}}}. Each has its own match and is laid over the
profile the same way, so the order is Default, then the profile, then the override.

Usage:
  python3 house_style.py resolve [--repo PATH] [--profile NAME[/OVERRIDE]]
      Print the merged profile for the repo as JSON, with every template as an
      absolute path and a "resolved" object saying which profile matched, why,
      which override applied ("override", "override_from"), and any warnings.
      --profile NAME still matches NAME's overrides; NAME/OVERRIDE forces one.
      Exits 3 when two profiles, or two overrides in one, match equally well ("resolved.from" is
      "ambiguous"): ask the user, then run again with --profile.
  python3 house_style.py validate DIR
      Check one profile folder; prints "ok" or one error per line (exit 1).

Matching, for the repo's `origin` remote and name (a worktree counts as its main
repo), ignoring case:
  - a "repos" entry equal to the repo name beats any remote glob;
  - otherwise the remote glob with the most literal characters wins;
  - a tie is ambiguous; no match falls back to Default.
  - a profile scores the best of its own match and its overrides' matches;
    inside the chosen profile the best-matching override applies, by the same rules.
Remotes are compared as host/path without scheme, credentials, port or ".git":
  git@github.com:acme/widget.git and https://github.com/acme/widget both read
  github.com/acme/widget.
"""

import argparse
import collections
import fnmatch
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, 'profiles', 'Default')
SCHEMA = 1
NAME = re.compile(r'[A-Z][A-Za-z0-9]*')
FILE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*\.md')
SKILL_REF = re.compile(r'skill:[a-z0-9-]+:[a-z0-9-]+')
PROJECT_KEY = re.compile(r'[A-Z][A-Z0-9]{1,9}')
TYPE_KEY = re.compile(r'[a-z][a-z0-9-]*')
SITE = re.compile(r'https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~-]+)*/?')
LABEL = re.compile(r'[^\x00-\x1f\x7f"`$\\]+')
FORGE_REPO = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)+')
CATCH_ALL_PROBES = ('probe.invalid/probe/probe', 'q.q/q/q')  # an override glob matching these matches nearly anything
TRACKERS = ('auto', 'jira', 'github', 'gitlab', 'none')
NULLABLE = (
    'tickets.types.*',
    'tickets.fields',
    'tickets.urgency',
    'on_ship.comment',
    'on_ship.transition',
    'tracker.project',
    'tracker.repo',
)
LAYER_KEYS = ('match', 'tracker', 'request', 'tickets', 'on_ship')
AMBIGUOUS = 3


class ProfileError(Exception):
    pass


# ---------------------------------------------------------------- locating


def config_path(env=None):
    env = os.environ if env is None else env
    base = env.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'clean-docs', 'config.json')


def profiles_dir(env=None):
    """The user's profiles directory, or None when none is configured."""
    env = os.environ if env is None else env
    if env.get('CLEAN_DOCS_PROFILES'):
        return os.path.expanduser(env['CLEAN_DOCS_PROFILES'])
    path = config_path(env)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8-sig') as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        raise ProfileError(f"{path}: can't be read as JSON ({e})") from e
    value = config.get('profiles_dir') if isinstance(config, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f'{path}: "profiles_dir" must be a path to the folder holding your profiles')
    return os.path.expanduser(value)


def available(pdir, warnings):
    """{name: folder} for every profile folder in pdir; a folder named Default there is ignored, with a warning."""
    if not pdir:
        return {}
    if not os.path.isdir(pdir):
        warnings.append(f'profiles directory {pdir} does not exist; using Default')
        return {}
    try:
        names = sorted(os.listdir(pdir))
    except OSError as e:
        raise ProfileError(f"profiles directory {pdir} can't be read ({e})") from e
    found = {}
    for name in names:
        if not (NAME.fullmatch(name) and os.path.isfile(os.path.join(pdir, name, 'profile.json'))):
            continue
        if name == 'Default':
            warnings.append(f'{pdir}/Default is ignored: Default is the built-in profile; rename yours')
            continue
        found[name] = os.path.join(pdir, name)
    return found


# ---------------------------------------------------------------- matching


def normalize_remote(url):
    """host/path of a git remote URL — no scheme, credentials, port or .git suffix, lower-case host — or None."""
    url = url.strip()
    m = re.fullmatch(r'(?i)[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:@]+)(?::\d+)?/(.+)', url)
    if not m:
        m = re.fullmatch(r'(?:[^@/:]+@)?([^/:@]+):(?!//)(.+)', url)  # scp-like: git@host:group/repo.git
    if not m:
        return None
    path = re.sub(r'\.git$', '', m.group(2).strip('/'))
    return m.group(1).lower() + '/' + path if path else None


def git(repo, *args):
    """(stdout, None) or (None, the first line of the error)."""
    try:
        proc = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=False)
    except OSError as e:
        return None, f'git could not run ({e})'
    if proc.returncode:
        return None, (proc.stderr.strip().splitlines() or ['git failed'])[0]
    return proc.stdout.strip(), None


def repo_facts(repo, warnings):
    """(normalized origin remote or None, repo name) for the git repo at repo; problems go to warnings.

    The repo name is the main repo's folder, so a linked worktree matches like its repo.
    """
    name = os.path.basename(os.path.abspath(repo))
    common, error = git(repo, 'rev-parse', '--git-common-dir')
    if error:
        warnings.append(f'{repo}: not matched as a git repo ({error})')
        return None, name
    common = os.path.abspath(os.path.join(repo, common))
    if os.path.basename(common) == '.git':
        name = os.path.basename(os.path.dirname(common))
    else:
        top, _ = git(repo, 'rev-parse', '--show-toplevel')
        name = os.path.basename(top) if top else re.sub(r'\.git$', '', os.path.basename(common))
    url, error = git(repo, 'remote', 'get-url', 'origin')
    if error:
        warnings.append(f'{name}: no origin remote ({error}); only "repos" entries can match')
        return None, name
    remote = normalize_remote(url)
    if remote is None:
        warnings.append(f'{name}: the origin remote isn\'t a URL this tool understands; only "repos" entries can match')
    return remote, name


def is_catch_all(glob):
    return any(fnmatch.fnmatchcase(probe, glob.lower()) for probe in CATCH_ALL_PROBES)


def literal_length(pattern):
    return len(re.sub(r'[*?\[\]]', '', pattern))


def match_score(match, remote, repo_name):
    """How well a profile's "match" fits; None when it doesn't (or is malformed — validate reports that)."""
    if not isinstance(match, dict):
        return None
    repos = match.get('repos') if isinstance(match.get('repos'), list) else []
    if repo_name.lower() in (r.lower() for r in repos if isinstance(r, str)):
        return float('inf')
    globs = match.get('remotes') if isinstance(match.get('remotes'), list) else []
    scores = [
        literal_length(g)
        for g in globs
        if isinstance(g, str) and remote and fnmatch.fnmatchcase(remote.lower(), g.lower())
    ]
    return max(scores) if scores else None


def overrides_of(data):
    """{name: layer} for a profile's well-formed overrides; malformed ones are left to validate."""
    overrides = data.get('overrides')
    if not isinstance(overrides, dict):
        return {}
    return {name: layer for name, layer in overrides.items() if NAME.fullmatch(name) and isinstance(layer, dict)}


def profile_score(data, remote, repo_name):
    """The best of a profile's own match and its overrides' matches; None when none fits."""
    matches = [data.get('match', {})] + [layer.get('match', {}) for layer in overrides_of(data).values()]
    scores = [s for s in (match_score(m, remote, repo_name) for m in matches) if s is not None]
    return max(scores) if scores else None


def choose_override(profile, data, remote, repo_name):
    """(override or None, how, tied) inside the chosen profile — how is repos | remote | ambiguous, or None."""
    scored = []
    for name, layer in overrides_of(data).items():
        score = match_score(layer.get('match', {}), remote, repo_name)
        if score is not None:
            scored.append((score, name))
    if not scored:
        return None, None, []
    best = max(s for s, _ in scored)
    tied = sorted(n for s, n in scored if s == best)
    if len(tied) > 1:
        return None, 'ambiguous', [f'{profile}/{n}' for n in tied]
    return tied[0], 'repos' if best == float('inf') else 'remote', []


def choose(profiles, remote, repo_name, warnings, cache=None):
    """(name or None, how, tied) — how is repos | remote | default | ambiguous; ambiguous also returns the tied names.

    A profile whose profile.json can't be read is skipped with a warning, so one broken profile
    doesn't stop every other repo from resolving.
    """
    scored = []
    for name, folder in profiles.items():
        try:
            data = read_json(folder)
        except ProfileError as e:
            warnings.append(f'skipped profile {name}: {e}')
            continue
        if cache is not None:
            cache[name] = data
        score = profile_score(data, remote, repo_name)
        if score is not None:
            scored.append((score, name))
    if not scored:
        return None, 'default', []
    best = max(s for s, _ in scored)
    tied = sorted(n for s, n in scored if s == best)
    if len(tied) > 1:
        return None, 'ambiguous', tied
    return tied[0], 'repos' if best == float('inf') else 'remote', []


# ---------------------------------------------------------------- loading


def forge_repo_ok(value):
    """owner/repo or group/sub/repo — never host/owner/repo, which gh and glab would read as another host."""
    if not (isinstance(value, str) and FORGE_REPO.fullmatch(value)):
        return False
    parts = value.split('/')
    if len(parts) > 2 and '.' in parts[0]:
        return False
    return not any(part.lower().endswith('.git') for part in parts)


def no_duplicate_keys(pairs):
    counts = collections.Counter(k for k, _ in pairs)
    dupes = sorted(k for k, n in counts.items() if n > 1)
    if dupes:
        raise ValueError(f'duplicate keys {dupes}; only the last of each would count')
    return dict(pairs)


def inside(path, root):
    """True when path, symlinks resolved, is root or below it."""
    real, base = (os.path.normcase(os.path.realpath(p)) for p in (path, root))
    return os.path.commonpath([real, base]) == base


def read_json(folder):
    path = os.path.join(folder, 'profile.json')
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f, object_pairs_hook=no_duplicate_keys)
    except (OSError, ValueError) as e:
        raise ProfileError(f'{path}: {e}') from e
    if not isinstance(data, dict):
        raise ProfileError(f'{path}: must be a JSON object')
    return data


def merge(base, over):
    """Lay over onto base: objects merge key by key, null removes, anything else replaces."""
    out = dict(base)
    for key, value in over.items():
        if value is None:
            out.pop(key, None)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def null_paths(obj, prefix=''):
    """Dotted paths of every null value in obj."""
    for key, value in obj.items():
        path = f'{prefix}{key}'
        if value is None:
            yield path
        elif isinstance(value, dict):
            yield from null_paths(value, path + '.')


def validate(data, where, folder):
    """Return a list of errors for one profile.json (raw, before merging), its overrides included."""
    errors = validate_layer(data, where, folder, override=False)
    if not isinstance(data, dict) or 'overrides' not in data:
        return errors
    overrides = data['overrides']
    if not isinstance(overrides, dict):
        if overrides is not None:  # null is reported by validate_layer
            errors.append(f'{where}: "overrides" must be an object of named overrides')
        return errors
    for name, layer in overrides.items():
        label = f'{where} overrides.{name}'
        if not NAME.fullmatch(name):
            errors.append(f'{label}: override names are PascalCase, like profile names')
        if not isinstance(layer, dict):
            errors.append(f'{label}: must be an object with a "match" and the settings it overrides')
            continue
        errors += validate_layer(layer, label, folder, override=True)
    return errors


def validate_layer(data, where, folder, override):
    """Errors for one layer: the profile itself, or one of its overrides."""
    if not isinstance(data, dict):
        return [f'{where}: must be a JSON object']
    errors = []

    def err(msg):
        errors.append(f'{where}: {msg}')

    def check_keys(obj, allowed, label):
        if not isinstance(obj, dict):
            err(f'"{label}" must be an object')
            return False
        for key in obj:
            if key not in allowed:
                err(f'unknown key "{label}.{key}"' if label else f'unknown key "{key}"')
        return True

    def check_file(value, label, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or not FILE.fullmatch(value):
            err(f'"{label}" must be a template file name such as Template.md, in the profile folder')
        elif os.path.lexists(os.path.join(folder, value)) and not inside(os.path.join(folder, value), folder):
            err(f'"{label}": {value} links outside the profile folder; templates must live in it')
        elif not (os.path.isfile(os.path.join(folder, value)) or os.path.isfile(os.path.join(DEFAULT_DIR, value))):
            err(f'"{label}": {value} is in neither this profile nor Default')

    scan = {k: v for k, v in data.items() if k != 'overrides' or v is None}
    for path in null_paths(scan):
        if not any(fnmatch.fnmatchcase(path, allowed) for allowed in NULLABLE):
            err(f'"{path}" can\'t be null; null removes only {", ".join(NULLABLE)}')
    data = {k: v for k, v in data.items() if v is not None}

    if override:
        check_keys(data, LAYER_KEYS, '')
        if 'match' not in data:
            err('an override needs a "match" saying which repos it applies to')
    else:
        check_keys(data, ('schema', *LAYER_KEYS, 'overrides'), '')
        schema = data.get('schema')
        if type(schema) is not int or schema != SCHEMA:
            err(f'"schema" must be {SCHEMA}')

    match = data.get('match', {})
    if check_keys(match, ('remotes', 'repos'), 'match'):
        for key in ('remotes', 'repos'):
            value = match.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                err(f'"match.{key}" must be a list of non-empty strings')
        if override and 'match' in data and not any(match.get(k) for k in ('remotes', 'repos')):
            err('"match" needs at least one entry in "repos" or "remotes"')
        globs = match.get('remotes', [])
        if override and isinstance(globs, list) and any(is_catch_all(g) for g in globs if isinstance(g, str)):
            err('"match.remotes" can\'t hold a glob that matches every repo; an override is for some repos only')

    tracker = data.get('tracker', {})
    if check_keys(tracker, ('kind', 'site', 'link_projects', 'project', 'repo'), 'tracker'):
        if tracker.get('project') is not None and not (
            isinstance(tracker['project'], str) and PROJECT_KEY.fullmatch(tracker['project'])
        ):
            err('"tracker.project" must be a Jira project key such as "ABC"')
        if tracker.get('repo') is not None and not forge_repo_ok(tracker['repo']):
            err(
                '"tracker.repo" must be a forge repo path such as "owner/repo" or "group/sub/repo",'
                ' with no host and no .git'
            )
        if 'kind' in tracker and tracker['kind'] not in TRACKERS:
            err(f'"tracker.kind" must be one of {list(TRACKERS)}')
        if 'site' in tracker and not (isinstance(tracker['site'], str) and SITE.fullmatch(tracker['site'])):
            err('"tracker.site" must be an https URL such as https://example.atlassian.net (a port and path are fine)')
        keys = tracker.get('link_projects', [])
        if not isinstance(keys, list) or not all(isinstance(k, str) and PROJECT_KEY.fullmatch(k) for k in keys):
            err('"tracker.link_projects" must be a list of project keys such as ["ABC"]')

    request = data.get('request', {})
    if check_keys(request, ('template', 'draft'), 'request'):
        template = request.get('template')
        if isinstance(template, str) and template.startswith('skill:'):
            if not SKILL_REF.fullmatch(template):
                err('"request.template" skill reference must look like skill:<plugin>:<skill>')
        elif 'template' in request:
            check_file(template, 'request.template')
        if 'draft' in request and not isinstance(request['draft'], bool):
            err('"request.draft" must be true or false')

    tickets = data.get('tickets', {})
    if check_keys(tickets, ('types', 'fields', 'urgency', 'max_summary'), 'tickets'):
        types = tickets.get('types', {})
        if isinstance(types, dict):
            for key, spec in types.items():
                if not TYPE_KEY.fullmatch(key):
                    err(f'"tickets.types.{key}": type keys are lower-case words such as "story"')
                if spec is None:
                    continue
                if not check_keys(spec, ('name', 'labels', 'template', 'urgency'), f'tickets.types.{key}'):
                    continue
                if 'name' in spec and not (isinstance(spec['name'], str) and spec['name'].strip()):
                    err(f'"tickets.types.{key}.name" must be the tracker\'s issue type name')
                labels = spec.get('labels', [])
                if not isinstance(labels, list) or not all(
                    isinstance(v, str) and v.strip() and LABEL.fullmatch(v) for v in labels
                ):
                    err(f'"tickets.types.{key}.labels" must be a list of label names without quotes, $, ` or \\')
                if 'template' in spec:
                    check_file(spec['template'], f'tickets.types.{key}.template')
                if 'urgency' in spec and not isinstance(spec['urgency'], bool):
                    err(f'"tickets.types.{key}.urgency" must be true or false')
        elif 'types' in tickets:
            err('"tickets.types" must be an object of types; remove one type with "types": {"<type>": null}')
        for key in ('fields', 'urgency'):
            if key in tickets:
                check_file(tickets[key], f'tickets.{key}', nullable=True)
        limit = tickets.get('max_summary', 80)
        if type(limit) is not int or not 20 <= limit <= 255:
            err('"tickets.max_summary" must be a whole number from 20 to 255')

    on_ship = data.get('on_ship', {})
    if check_keys(on_ship, ('comment', 'transition'), 'on_ship'):
        if 'comment' in on_ship:
            check_file(on_ship['comment'], 'on_ship.comment', nullable=True)
        transition = on_ship.get('transition')
        if transition is not None and not (isinstance(transition, str) and transition.strip()):
            err('"on_ship.transition" must be a status transition name, or null for none')
    return errors


def load(name, folder, remote=None, override=None, raw=None, infer=True):
    """The merged, validated profile with absolute template paths: Default, then the profile, then the override.

    raw is the profile's already-read profile.json, if any. infer=False (validate DIR, where there is no repo)
    skips the checks that depend on the kind an "auto" tracker infers from the remote.
    """
    default = read_json(DEFAULT_DIR)
    errors = validate(default, 'Default/profile.json', DEFAULT_DIR)
    if folder == DEFAULT_DIR:
        raw = {}
    else:
        raw = read_json(folder) if raw is None else raw
        errors += validate(raw, f'{name}/profile.json', folder)
    if errors:
        raise ProfileError('\n'.join(errors))
    layer = {}
    if override is not None:
        if override not in overrides_of(raw):
            raise ProfileError(f'{name} has no override named {override}; its overrides: {sorted(overrides_of(raw))}')
        layer = {k: v for k, v in raw['overrides'][override].items() if k != 'match'}
    base = merge(default, {k: v for k, v in raw.items() if k != 'overrides'})
    merged = merge(base, layer)
    in_layer = f'{name}/{override}'

    def path(file):
        if file is None:
            return None
        own = os.path.join(folder, file)
        if os.path.lexists(own) and not inside(own, folder):
            raise ProfileError(f'{name}: template {file} links outside the profile folder; templates must live in it')
        return own if os.path.isfile(own) else os.path.join(DEFAULT_DIR, file)

    tracker = dict(merged.get('tracker', {}))
    layer_tracker = layer.get('tracker', {})
    declared = tracker.get('kind', 'auto')
    if declared == 'auto':
        tracker['kind'] = infer_tracker(remote)
    kind = tracker['kind'] if infer or declared != 'auto' else None  # None: not known without a repo

    base_kind = base.get('tracker', {}).get('kind', 'auto')

    def blame(key):
        """Which layer to fix: the override when it set the key, or changed the kind under it."""
        if override and key in layer_tracker:
            return in_layer, ''
        if override and layer_tracker.get('kind', base_kind) != base_kind:
            return in_layer, f'; it changes the kind, so set "tracker.{key}" to null there'
        return name, ''

    # auto never infers jira, so a project on auto is wrong whatever the remote
    if 'project' in tracker and (declared == 'auto' or kind != 'jira'):
        who, hint = blame('project')
        shown = tracker['kind'] if infer or declared != 'auto' else 'auto (github, gitlab or none)'
        raise ProfileError(f'{who}: "tracker.project" is a Jira project key, but the tracker is {shown}{hint}')
    if 'repo' in tracker and kind is not None:
        who, hint = blame('repo')
        if kind not in ('github', 'gitlab'):
            raise ProfileError(f'{who}: "tracker.repo" is a forge repo, but the tracker is {kind}{hint}')
        if kind == 'github' and tracker['repo'].count('/') != 1:
            raise ProfileError(f'{who}: on GitHub "tracker.repo" is owner/repo, not {tracker["repo"]}')
    if tracker['kind'] == 'jira' and not tracker.get('site'):
        who = in_layer if override else name
        raise ProfileError(f'{who}: a jira tracker needs "tracker.site"')
    projects = [base.get('tracker', {}).get('project'), tracker.get('project')] if tracker['kind'] == 'jira' else []
    links = [*tracker.get('link_projects', []), *(p for p in projects if p)]
    tracker['link_projects'] = list(dict.fromkeys(links))

    request = dict(merged.get('request', {}))
    if not str(request.get('template', '')).startswith('skill:'):
        request['template'] = path(request.get('template'))
    tickets = dict(merged.get('tickets', {}))
    for key, spec in tickets.get('types', {}).items():
        missing = [k for k in ('name', 'template') if k not in spec]
        if missing:
            who = in_layer if override and key in layer.get('tickets', {}).get('types', {}) else name
            raise ProfileError(f'{who}: ticket type "{key}" needs {missing} (it is not one of Default\'s types)')
    tickets['types'] = {
        key: {'labels': [], **spec, 'template': path(spec['template'])}
        for key, spec in tickets.get('types', {}).items()
    }
    for key in ('fields', 'urgency'):
        tickets[key] = path(tickets.get(key))
    on_ship = dict(merged.get('on_ship', {}))
    on_ship['comment'] = path(on_ship.get('comment'))
    on_ship.setdefault('transition', None)
    return {'tracker': tracker, 'request': request, 'tickets': tickets, 'on_ship': on_ship}


def infer_tracker(remote):
    """github or gitlab from the remote's host, else none — a self-hosted forge needs tracker.kind set in a profile."""
    host = (remote or '').split('/', 1)[0]
    if host == 'github.com':
        return 'github'
    if 'gitlab' in host:
        return 'gitlab'
    return 'none'


def resolve(repo='.', force=None, env=None):
    """The merged profile for repo, plus a "resolved" object: {name, from, dir, candidates, remote, warnings}."""
    warnings = []
    remote, repo_name = repo_facts(repo, warnings)
    profiles = available(profiles_dir(env), warnings)
    forced_override, cache = None, {}
    if force and '/' in force:
        force, _, forced_override = force.partition('/')
        if not (NAME.fullmatch(force) and NAME.fullmatch(forced_override)):
            raise ProfileError(f'--profile takes Name or Name/Override, not {force}/{forced_override}')
        if force == 'Default':
            raise ProfileError('Default has no overrides')
    if force:
        if force == 'Default':
            name, how, tied = 'Default', 'forced', []
        elif force in profiles:
            name, how, tied = force, 'forced', []
        else:
            raise ProfileError(f'no profile named {force}; available: {["Default", *profiles]}')
    else:
        name, how, tied = choose(profiles, remote, repo_name, warnings, cache)
    resolved = {
        'name': None,
        'from': how,
        'override': None,
        'override_from': None,
        'dir': None,
        'candidates': tied,
        'remote': remote,
        'warnings': warnings,
    }
    if how == 'ambiguous':
        return {'resolved': resolved}
    name = name or 'Default'
    folder = DEFAULT_DIR if name == 'Default' else profiles[name]
    raw = None if name == 'Default' else cache[name] if name in cache else read_json(folder)
    override, override_how = None, None
    if forced_override:
        override, override_how = forced_override, 'forced'
    elif raw is not None:
        override, override_how, override_ties = choose_override(name, raw, remote, repo_name)
        if override_how == 'ambiguous':
            return {'resolved': dict(resolved, **{'from': 'ambiguous', 'candidates': override_ties})}
    profile = load(name, folder, remote, override, raw)
    profile['resolved'] = dict(resolved, name=name, dir=folder, override=override, override_from=override_how)
    return profile


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('resolve', help='print the merged profile for a repo')
    r.add_argument('--repo', default='.')
    r.add_argument('--profile', help='use this profile instead of matching')
    v = sub.add_parser('validate', help='check one profile folder')
    v.add_argument('dir')
    args = ap.parse_args(argv)
    try:
        if args.cmd == 'validate':
            folder = os.path.abspath(args.dir)
            name = os.path.basename(folder)
            errors = [] if NAME.fullmatch(name) else [f'{name}: profile folders are named in PascalCase']
            raw = read_json(folder)
            errors += validate(raw, f'{name}/profile.json', folder)
            if not errors:
                for override in (None, *overrides_of(raw)):
                    try:
                        load(name, folder, override=override, raw=raw, infer=False)
                    except ProfileError as e:
                        errors.append(str(e))
            errors = list(dict.fromkeys(errors))
            print('\n'.join(errors) if errors else 'ok')
            return 1 if errors else 0
        profile = resolve(args.repo, args.profile)
    except ProfileError as e:
        print(f'profile error: {e}', file=sys.stderr)
        return 1
    print(json.dumps(profile, indent=2))
    return AMBIGUOUS if profile['resolved']['from'] == 'ambiguous' else 0


if __name__ == '__main__':
    sys.exit(main())
