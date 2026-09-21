"""Reciprocal rank fusion with stable tie breaking; no third-party packages."""
import unittest


def rrf(rankings, *, constant=60, limit=10):
    if type(constant) is not int or constant < 0:
        raise ValueError("constant must be a nonnegative integer")
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    scores = {}
    for ranking in rankings:
        seen = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if not isinstance(doc_id, str) or not doc_id:
                raise ValueError("document ids must be nonempty strings")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1 / (constant + rank)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return ordered[:limit]


class Tests(unittest.TestCase):
    def test_two_retrievers(self):
        result = rrf([["a", "b", "c"], ["c", "b", "d"]])
        self.assertEqual([doc for doc, _ in result], ["c", "b", "a", "d"])

    def test_duplicate_gets_no_extra_vote(self):
        self.assertEqual(rrf([["a", "a"]]), rrf([["a"]]))

    def test_stable_tie(self):
        self.assertEqual([doc for doc, _ in rrf([["z"], ["a"]])], ["a", "z"])

    def test_empty_and_zero_limit(self):
        self.assertEqual(rrf([[], []]), [])
        self.assertEqual(rrf([["a"]], limit=0), [])

    def test_invalid(self):
        for options in ({"constant": -1}, {"limit": True}, {"limit": -2}):
            with self.assertRaises(ValueError):
                rrf([["a"]], **options)
        with self.assertRaises(ValueError):
            rrf([[""]])


if __name__ == "__main__":
    unittest.main()
