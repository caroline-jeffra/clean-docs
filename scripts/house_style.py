"""Resolve and load clean-docs house-style profiles.

A profile is a folder named in PascalCase holding a profile.json and the Markdown
templates it names. Profiles live outside this plugin, in a profiles directory:

  $CLEAN_DOCS_PROFILES, else "profiles_dir" in
  $XDG_CONFIG_HOME/clean-docs/config.json (default ~/.config/clean-docs/config.json).

The plugin ships one profile, Default, under profiles/Default. Every other profile
is laid over it: objects merge key by key, anything else replaces, and a null
removes the key (e.g. "types": {"task": null}). A template named in a profile is
looked up in that profile's folder first, then in Default.

Usage:
  python3 house_style.py resolve [--repo PATH] [--profile NAME]
      Print the merged profile for the repo as JSON, with every template as an
      absolute path and a "resolved" object saying which profile matched and why.
      Exits 3 when two profiles match equally well ("resolved.from" is
      "ambiguous"): ask the user, then run again with --profile.
  python3 house_style.py validate DIR
      Check one profile folder; prints "ok" or one error per line (exit 1).

Matching, for the repo's `origin` remote and folder name:
  - a "repos" entry equal to the repo folder name beats any remote glob;
  - otherwise the remote glob with the most literal characters wins;
  - a tie is ambiguous; no match falls back to Default.
Remotes are compared as host/path without scheme, user, port or ".git":
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
TRACKERS = ('auto', 'jira', 'github', 'gitlab', 'none')
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
    with open(path, encoding='utf-8-sig') as f:
        config = json.load(f)
    value = config.get('profiles_dir') if isinstance(config, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f'{path}: "profiles_dir" must be a path to the folder holding your profiles')
    return os.path.expanduser(value)


def available(pdir):
    """{name: folder} for every profile folder in pdir (Default is never taken from there)."""
    if not pdir or not os.path.isdir(pdir):
        return {}
    return {
        name: os.path.join(pdir, name)
        for name in sorted(os.listdir(pdir))
        if NAME.fullmatch(name) and name != 'Default' and os.path.isfile(os.path.join(pdir, name, 'profile.json'))
    }


# ---------------------------------------------------------------- matching


def normalize_remote(url):
    """host/path of a git remote URL: no scheme, user, port or .git suffix, lower-case host."""
    url = url.strip()
    m = re.fullmatch(r'[a-z+]+://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)', url)
    if not m:
        m = re.fullmatch(r'(?:[^@/]+@)?([^/:]+):(.+)', url)  # scp-like: git@host:group/repo.git
    if not m:
        return url
    path = re.sub(r'\.git$', '', m.group(2).strip('/'))
    return m.group(1).lower() + '/' + path


def repo_facts(repo):
    """(normalized origin remote or None, repo folder name) for the git repo at repo."""
    top = repo
    try:
        top = subprocess.run(
            ['git', '-C', repo, 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True
        ).stdout.strip()
        url = subprocess.run(
            ['git', '-C', repo, 'remote', 'get-url', 'origin'], capture_output=True, text=True, check=True
        ).stdout.strip()
        remote = normalize_remote(url) if url else None
    except (OSError, subprocess.CalledProcessError):
        remote = None
    return remote, os.path.basename(os.path.abspath(top))


def literal_length(pattern):
    return len(re.sub(r'[*?\[\]]', '', pattern))


def match_score(match, remote, repo_name):
    """How well a profile's "match" fits; None when it doesn't (or is malformed — validate reports that)."""
    if not isinstance(match, dict):
        return None
    if isinstance(match.get('repos'), list) and repo_name in match['repos']:
        return float('inf')
    globs = (
        [g for g in match.get('remotes', []) if isinstance(g, str)] if isinstance(match.get('remotes'), list) else []
    )
    scores = [literal_length(g) for g in globs if remote and fnmatch.fnmatchcase(remote, g)]
    return max(scores) if scores else None


def choose(profiles, remote, repo_name):
    """(name or None, how) — how is repos | remote | default | ambiguous; ambiguous also returns the tied names."""
    scored = []
    for name, folder in profiles.items():
        score = match_score(read_json(folder).get('match', {}), remote, repo_name)
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


def validate(data, where, folder):
    """Return a list of errors for one profile.json (raw, before merging)."""
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

    check_keys(data, ('schema', 'match', 'tracker', 'request', 'tickets', 'on_ship'), '')
    if data.get('schema') != SCHEMA:
        err(f'"schema" must be {SCHEMA}')

    match = data.get('match', {})
    if check_keys(match, ('remotes', 'repos'), 'match'):
        for key in ('remotes', 'repos'):
            value = match.get(key, [])
            if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
                err(f'"match.{key}" must be a list of non-empty strings')

    tracker = data.get('tracker', {})
    if check_keys(tracker, ('kind', 'site', 'link_projects'), 'tracker'):
        if 'kind' in tracker and tracker['kind'] not in TRACKERS:
            err(f'"tracker.kind" must be one of {list(TRACKERS)}')
        if 'site' in tracker and not (
            isinstance(tracker['site'], str) and re.fullmatch(r'https://[A-Za-z0-9.-]+/?', tracker['site'])
        ):
            err('"tracker.site" must be an https URL such as https://example.atlassian.net')
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
        if check_keys(types, types.keys(), 'tickets.types'):
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
                if not isinstance(labels, list) or not all(isinstance(v, str) and v.strip() for v in labels):
                    err(f'"tickets.types.{key}.labels" must be a list of issue label names')
                if 'template' in spec:
                    check_file(spec['template'], f'tickets.types.{key}.template')
                if 'urgency' in spec and not isinstance(spec['urgency'], bool):
                    err(f'"tickets.types.{key}.urgency" must be true or false')
        for key in ('fields', 'urgency'):
            if key in tickets:
                check_file(tickets[key], f'tickets.{key}', nullable=True)
        if 'max_summary' in tickets and not (
            isinstance(tickets['max_summary'], int) and 20 <= tickets['max_summary'] <= 255
        ):
            err('"tickets.max_summary" must be a whole number from 20 to 255')

    on_ship = data.get('on_ship', {})
    if check_keys(on_ship, ('comment', 'transition'), 'on_ship'):
        if 'comment' in on_ship:
            check_file(on_ship['comment'], 'on_ship.comment', nullable=True)
        transition = on_ship.get('transition')
        if transition is not None and not (isinstance(transition, str) and transition.strip()):
            err('"on_ship.transition" must be a status transition name, or null for none')
    return errors


def load(name, folder, remote=None):
    """The merged, validated profile with absolute template paths."""
    default = read_json(DEFAULT_DIR)
    errors = validate(default, 'Default/profile.json', DEFAULT_DIR)
    raw = {}
    if folder != DEFAULT_DIR:
        raw = read_json(folder)
        errors += validate(raw, f'{name}/profile.json', folder)
    if errors:
        raise ProfileError('\n'.join(errors))
    merged = merge(default, raw)

    def path(file):
        if file is None:
            return None
        own = os.path.join(folder, file)
        return own if os.path.isfile(own) else os.path.join(DEFAULT_DIR, file)

    tracker = dict(merged.get('tracker', {}))
    if tracker.get('kind', 'auto') == 'auto':
        tracker['kind'] = infer_tracker(remote)
    if tracker['kind'] == 'jira' and not tracker.get('site'):
        raise ProfileError(f'{name}: a jira tracker needs "tracker.site"')
    tracker.setdefault('link_projects', [])

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
    host = (remote or '').split('/', 1)[0]
    if host == 'github.com':
        return 'github'
    if 'gitlab' in host:
        return 'gitlab'
    return 'none'


def resolve(repo='.', force=None, env=None):
    """The merged profile for repo, plus a "resolved" object: {name, from, dir, candidates, remote}."""
    remote, repo_name = repo_facts(repo)
    profiles = available(profiles_dir(env))
    if force:
        if force == 'Default':
            name, how, tied = 'Default', 'forced', []
        elif force in profiles:
            name, how, tied = force, 'forced', []
        else:
            raise ProfileError(f'no profile named {force}; available: {["Default", *profiles]}')
    else:
        name, how, tied = choose(profiles, remote, repo_name)
    if how == 'ambiguous':
        return {'resolved': {'name': None, 'from': how, 'dir': None, 'candidates': tied, 'remote': remote}}
    name = name or 'Default'
    folder = DEFAULT_DIR if name == 'Default' else profiles[name]
    profile = load(name, folder, remote)
    profile['resolved'] = {'name': name, 'from': how, 'dir': folder, 'candidates': [], 'remote': remote}
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
