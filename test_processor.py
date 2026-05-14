import json
import unittest

from processor import _parse_response


class ParseResponseTests(unittest.TestCase):
    def test_skips_people_without_valid_surname(self):
        text = json.dumps(
            {
                "persons": [
                    {"surname": "Коваленко", "name": "Іван"},
                    {"name": "Марія"},
                    {"surname": None, "name": "Петро"},
                    {"surname": " null ", "name": "Олена"},
                    {"surname": "NULL", "name": "Степан"},
                    {"surname": "Шевченко", "name": "Тарас"},
                ]
            },
            ensure_ascii=False,
        )

        persons, scan_meta = _parse_response(text, extended_used=False)

        self.assertIsNone(scan_meta)
        self.assertEqual(
            persons,
            [
                {"surname": "Коваленко", "name": "Іван", "meta": None},
                {"surname": "Шевченко", "name": "Тарас", "meta": None},
            ],
        )


if __name__ == "__main__":
    unittest.main()
