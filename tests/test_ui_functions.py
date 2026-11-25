"""Статическая проверка интерфейса: все функции, которые зовёт boot(), существуют.

Именно из-за отсутствия такой проверки экран настроек молча перестал открываться:
функции удалили при рефакторинге, а вызов остался.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "web" / "app.js"

# функции, без которых интерфейс стартует с пустым экраном или мёртвыми кнопками
REQUIRED = [
    "checkHealth", "loadFontCatalog", "loadProjects", "openProject", "renderMain",
    "bindUpdateCard", "bindHud", "pollSystem", "checkUpdates", "openSettings",
    "chooseFolder", "initPip", "renderSystem", "showFatal", "withRetry",
]


class UiFunctionsTest(unittest.TestCase):
    def setUp(self):
        self.source = APP_JS.read_text(encoding="utf-8")
        self.defined = set(re.findall(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", self.source))
        self.defined |= set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", self.source))

    def test_required_functions_defined(self):
        missing = [name for name in REQUIRED if name not in self.defined]
        self.assertEqual(missing, [], f"в app.js нет функций: {missing}")

    def test_boot_references_are_defined(self):
        match = re.search(r"\(async function boot\(\) \{(.*?)\n\}\)\(\);", self.source, re.S)
        self.assertIsNotNone(match, "не найден boot()")
        body = match.group(1)
        called = set(re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(", body))
        known = {"if", "for", "while", "switch", "catch", "function", "return", "typeof",
                 "setTimeout", "setInterval", "clearTimeout", "Promise", "String", "Number",
                 "JSON", "location", "console", "fetch", "Boolean", "Array", "Object"}
        missing = [name for name in called if name not in self.defined and name not in known]
        self.assertEqual(missing, [], f"boot() зовёт несуществующие функции: {missing}")

    def test_settings_button_is_bound(self):
        self.assertIn('$("#settingsBtn")', self.source)
        self.assertIn("openSettings", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
