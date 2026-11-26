"""Resolve and load clean-docs house-style profiles.

A profile is a folder named in PascalCase holding a profile.json and the Markdown
templates it names. Profiles live outside this plugin, in a profiles directory:

  $CLEAN_DOCS_PROFILES, else "profiles_dir" in
  $XDG_CONFIG_HOME/clean-docs/config.json (default ~/.config/clean-docs/config.json).

The plugin ships one profile, Default, under profiles/Default. Every other profile
is laid over it: objects merge key by key, anything else replaces, and a null
removes the key. Null is allowed only where removing makes sense: a ticket type
("types": {"task": null}), tickets.fields, tickets.urgency, on_ship.comment and
on_ship.transition. A template named in a profile is looked up in that profile's
folder first, then in Default.

Usage:
  python3 house_style.py resolve [--repo PATH] [--profile NAME]
      Print the merged profile for the repo as JSON, with every template as an
      absolute path and a "resolved" object saying which profile matched, why,
      and any warnings (a skipped broken profile, a git problem).
      Exits 3 when two profiles match equally well ("resolved.from" is
      "ambiguous"): ask the user, then run again with --profile.
  python3 house_style.py validate DIR
      Check one profile folder; prints "ok" or one error per line (exit 1).

Matching, for the repo's `origin` remote and name (a worktree counts as its main
repo), ignoring case:
  - a "repos" entry equal to the repo name beats any remote glob;
  - otherwise the remote glob with the most literal characters wins;
  - a tie is ambiguous; no match falls back to Default.
Remotes are compared as host/path without scheme, credentials, port or ".git":
  git@github.com:acme/widget.git and https://github.com/acme/widget both read
  github.com/acme/widget.
"""

import argparse
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
    return {name: layer for name, layer in overrides.items() if isinstance(layer, dict)}


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


def choose(profiles, remote, repo_name, warnings):
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


def read_json(folder):
    path = os.path.join(folder, 'profile.json')
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
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
    if 'overrides' not in data or not isinstance(data, dict):
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

    tracker = data.get('tracker', {})
    if check_keys(tracker, ('kind', 'site', 'link_projects', 'project', 'repo'), 'tracker'):
        if tracker.get('project') is not None and not (
            isinstance(tracker['project'], str) and PROJECT_KEY.fullmatch(tracker['project'])
        ):
            err('"tracker.project" must be a Jira project key such as "ABC"')
        if tracker.get('repo') is not None and not (
            isinstance(tracker['repo'], str)
            and FORGE_REPO.fullmatch(tracker['repo'])
            and '..' not in tracker['repo'].split('/')
        ):
            err('"tracker.repo" must be a forge repo path such as "owner/repo" or "group/sub/repo"')
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


def load(name, folder, remote=None, override=None):
    """The merged, validated profile with absolute template paths: Default, then the profile, then the override."""
    default = read_json(DEFAULT_DIR)
    errors = validate(default, 'Default/profile.json', DEFAULT_DIR)
    raw = {}
    if folder != DEFAULT_DIR:
        raw = read_json(folder)
        errors += validate(raw, f'{name}/profile.json', folder)
    if errors:
        raise ProfileError('\n'.join(errors))
    layer = {}
    if override is not None:
        if override not in overrides_of(raw):
            raise ProfileError(f'{name} has no override named {override}; its overrides: {sorted(overrides_of(raw))}')
        layer = {k: v for k, v in raw['overrides'][override].items() if k != 'match'}
    base = {k: v for k, v in raw.items() if k != 'overrides'}
    merged = merge(merge(default, base), layer)

    def path(file):
        if file is None:
            return None
        own = os.path.join(folder, file)
        return own if os.path.isfile(own) else os.path.join(DEFAULT_DIR, file)

    tracker = dict(merged.get('tracker', {}))
    declared = tracker.get('kind', 'auto')

    def set_by(key):
        return f'{name}/{override}' if key in layer.get('tracker', {}) else name

    if 'project' in tracker and declared != 'jira':
        raise ProfileError(
            f'{set_by("project")}: "tracker.project" is a Jira project key, but the tracker is {declared}'
        )
    if 'repo' in tracker and declared not in ('github', 'gitlab', 'auto'):
        raise ProfileError(f'{set_by("repo")}: "tracker.repo" is a forge repo, but the tracker is {declared}')
    if declared == 'auto':
        tracker['kind'] = infer_tracker(remote)
    if tracker['kind'] == 'jira' and not tracker.get('site'):
        raise ProfileError(f'{name}: a jira tracker needs "tracker.site"')
    links = tracker.get('link_projects', [])
    tracker['link_projects'] = list(dict.fromkeys([*links, tracker['project']] if 'project' in tracker else links))

    request = dict(merged.get('request', {}))
    if not str(request.get('template', '')).startswith('skill:'):
        request['template'] = path(request.get('template'))
    tickets = dict(merged.get('tickets', {}))
    for key, spec in tickets.get('types', {}).items():
        missing = [k for k in ('name', 'template') if k not in spec]
        if missing:
            raise ProfileError(f'{name}: ticket type "{key}" needs {missing} (it is not one of Default\'s types)')
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
    forced_override = None
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
        name, how, tied = choose(profiles, remote, repo_name, warnings)
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
    override, override_how = None, None
    if forced_override:
        override, override_how = forced_override, 'forced'
    elif name != 'Default':
        override, override_how, tied = choose_override(name, read_json(folder), remote, repo_name)
        if override_how == 'ambiguous':
            return {'resolved': dict(resolved, **{'from': 'ambiguous', 'candidates': tied})}
    profile = load(name, folder, remote, override)
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
            errors += validate(read_json(folder), f'{name}/profile.json', folder)
            if not errors:
                load(name, folder)
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
