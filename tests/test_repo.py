"""Repo-wide consistency checks: skill metadata, README, plugin manifests, changelog and links."""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, 'skills')


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def frontmatter(text):
    match = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if not match:
        return None
    return dict(line.split(': ', 1) for line in match.group(1).splitlines() if ': ' in line)


def skill_dirs():
    return sorted(d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))


def markdown_links(root):
    """Yield (file path, relative link target) for every relative link in the markdown under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for filename in filenames:
            if not filename.endswith('.md'):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, encoding='utf-8') as f:
                text = re.sub(r'```.*?```', '', f.read(), flags=re.S)
            for target in re.findall(r'\]\(([^)\s]+)\)', text):
                if not (re.match(r'[a-z]+:', target) or target.startswith('#')):
                    yield path, target.split('#')[0]


class Skills(unittest.TestCase):
    def test_every_skill_has_valid_frontmatter(self):
        for name in skill_dirs():
            with self.subTest(name):
                meta = frontmatter(read('skills', name, 'SKILL.md'))
                self.assertIsNotNone(meta, 'SKILL.md must start with --- frontmatter ---')
                self.assertEqual(meta.get('name'), name)
                self.assertTrue(meta.get('description', '').strip())
                self.assertIn(meta.get('disable-model-invocation', 'false'), ('true', 'false'))

    def test_readme_table_lists_every_skill(self):
        rows = re.findall(r'^\| \*\*([a-z-]+)\*\* \| `/([a-z-]+)` \|', read('README.md'), re.M)
        self.assertTrue(all(a == b for a, b in rows), 'README row name and invocation differ')
        self.assertEqual(sorted(a for a, _ in rows), skill_dirs())

    def test_skills_link_only_inside_the_repo(self):
        root = os.path.realpath(ROOT)
        for name in skill_dirs():
            for path, target in markdown_links(os.path.join(SKILLS, name)):
                with self.subTest(skill=name, link=target):
                    resolved = os.path.realpath(os.path.join(os.path.dirname(path), target))
                    self.assertTrue(resolved.startswith(root + os.sep), 'a skill must not link outside the repo')


class Profiles(unittest.TestCase):
    def test_every_default_template_is_named_by_the_default_profile(self):
        folder = os.path.join(ROOT, 'profiles', 'Default')
        profile = read('profiles', 'Default', 'profile.json')
        for name in os.listdir(folder):
            if name.endswith('.md'):
                with self.subTest(name):
                    self.assertIn(f'"{name}"', profile, 'an unreferenced template is dead weight')


class Manifests(unittest.TestCase):
    def setUp(self):
        self.plugin = json.loads(read('.claude-plugin', 'plugin.json'))
        self.market = json.loads(read('.claude-plugin', 'marketplace.json'))

    def test_marketplace_entry_matches_plugin(self):
        (entry,) = [p for p in self.market['plugins'] if p['source'] == './']
        self.assertEqual(entry['name'], self.plugin['name'])
        self.assertEqual(entry['description'], self.plugin['description'])

    def test_changelog_top_entry_is_current_version(self):
        top = re.search(r'^## \[(\d+\.\d+\.\d+)\]', read('CHANGELOG.md'), re.M)
        self.assertIsNotNone(top, 'CHANGELOG.md needs a "## [x.y.z]" entry')
        self.assertEqual(top.group(1), self.plugin['version'])


class Links(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        for path, target in markdown_links(ROOT):
            with self.subTest(file=os.path.relpath(path, ROOT), link=target):
                self.assertTrue(os.path.exists(os.path.join(os.path.dirname(path), target)))


if __name__ == '__main__':
    unittest.main()
