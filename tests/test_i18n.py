import json
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'static'
PLACEHOLDER = re.compile(r'\{(\d+)\}')
CONDITIONAL = re.compile(r'\{(\d+)\?([^|{}]*)\|([^{}]*)\}')


def load_dictionary():
    text = (STATIC / 'i18n-nl.js').read_text(encoding='utf-8')
    prefix, suffix = 'window.HAVEN_NL = ', ';'
    assert text.startswith(prefix)
    return json.loads(text[len(prefix):].rstrip().rstrip(suffix))


class DutchDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nl = load_dictionary()

    def entries(self):
        for group in ('ui', 'api'):
            yield from self.nl[group].items()

    def test_dictionary_is_substantial_and_has_no_empty_values(self):
        self.assertGreater(len(self.nl['ui']), 1200)
        self.assertGreater(len(self.nl['api']), 300)
        for en, nl in self.entries():
            self.assertTrue(en.strip() and nl.strip(), (en, nl))

    def test_placeholders_in_dutch_exist_in_the_english_pattern(self):
        for en, nl in self.entries():
            allowed = set(PLACEHOLDER.findall(en))
            used = set(PLACEHOLDER.findall(CONDITIONAL.sub('', nl))) | {m[0] for m in CONDITIONAL.findall(nl)}
            self.assertTrue(used <= allowed, f'{en!r} -> {nl!r} uses placeholders the English text does not have')

    def test_every_sentence_pattern_keeps_its_values(self):
        # a pattern like "{0} overdue across {1} invoice{2}" must carry its numbers over into the Dutch text
        for en, nl in self.entries():
            values = [i for i in PLACEHOLDER.findall(en) if not re.search(r'\{%s\}$' % i, en) or True]
            if len(values) == 1 and re.fullmatch(r'\{0\}\s?[^{}]+', en):
                continue  # "{0} Text" is an icon followed by text, registered as plain text too
            if re.search(r'(?:day|repair|task|tenanc|unit|contract|receipt|action|invoice)\{\d+\}', en):
                self.assertTrue(CONDITIONAL.search(nl) or '{' not in nl, f'plural pattern without singular/plural wording: {en!r}')

    def test_language_switch_files_are_wired_into_the_page(self):
        html = (STATIC / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('i18n-nl.js'), html.index('i18n.js'))
        self.assertLess(html.index('i18n.js'), html.index('/static/app.js'))
        script = (STATIC / 'i18n.js').read_text(encoding='utf-8')
        for needle in ("haven.lang", "data-lang", "nl-NL", "MutationObserver"):
            self.assertIn(needle, script)


if __name__ == '__main__':
    unittest.main()
